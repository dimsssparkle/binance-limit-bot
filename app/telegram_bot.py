# app/telegram_bot.py

import logging
from threading import Lock
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest
from app.config import settings
from app.binance_client import (
    get_position_amount,
    cancel_open_orders,
    place_post_only_with_retries,
    _client,
)
from typing import Dict, Any

# Убираем излишние логи httpx
logging.getLogger("httpx").setLevel(logging.WARNING)

# Общая настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

# Состояние бота
webhook_lock = Lock()
webhook_paused = False

# Хранилища плеча и данных по сделке
leverage_map: Dict[str, int] = {}
trade_records: Dict[str, Dict[str, Any]] = {}


def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = True
    return update.message.reply_text("Webhooks processing paused.")


def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = False
    return update.message.reply_text("Webhooks processing resumed.")


def is_entry_trade(fill: dict, is_entry: bool) -> bool:
    # fill['buyer']==True для входных (BUY), False для выходных (SELL)
    return (fill.get("buyer") is True) is is_entry


async def sum_commission_from_fills(fills: list[dict]) -> float:
    """Суммирует USDT-комиссию из списка fills."""
    return sum(
        float(f["commission"])
        for f in fills
        if f.get("commissionAsset") == "USDT"
    )


async def active_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = _client.futures_position_information()
    messages = []
    for p in positions:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        sym = p["symbol"]
        side = "LONG" if amt > 0 else "SHORT"
        # Берём данные позиции
        info = _client.futures_position_information(symbol=sym)
        pos = next(x for x in info if x["symbol"] == sym)
        entry_price   = float(pos.get("entryPrice", 0.0))
        margin_used   = float(pos.get("initialMargin", 0.0))
        liq_price     = float(pos.get("liquidationPrice", 0.0))
        mark_price    = float(_client.futures_mark_price(symbol=sym)["markPrice"])
        pnl_gross     = (mark_price - entry_price) * amt
        entry_comm    = await sum_commission_from_fills(
                            _client.futures_get_order_trades(symbol=sym, orderId=pos["entryOrderId"])
                        ) if "entryOrderId" in pos else 0.0
        exit_comm     = 0.0  # при активной позиции выход ещё не сделан
        pnl_net       = pnl_gross - entry_comm - exit_comm
        lev           = leverage_map.get(sym, int(pos.get("leverage", 1)))

        msg = (
            f"Символ: {sym}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Плечо: {lev}\n"
            f"Использованная маржа: {margin_used:.8f}\n"
            f"Цена ликвидации: {liq_price}\n"
            f"PNL брутто: {pnl_gross:.8f}\n"
            f"Комиссия входа: {entry_comm:.8f}\n"
            f"Комиссия выхода: {exit_comm:.8f}\n"
            f"PNL нетто: {pnl_net:.8f}"
        )
        messages.append(msg)

    text = "\n\n".join(messages) or "No active positions."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 4 or len(args) > 5:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Usage: /create_order <SYMBOL> <BUY|SELL> <AMOUNT> <LEVERAGE> [PRICE]"
        )
        return

    sym      = args[0].upper()
    side     = args[1].upper()
    amt      = float(args[2])
    leverage = int(args[3])
    price    = float(args[4]) if len(args) == 5 else None

    # Закрываем противоположную позицию, если есть
    signed_amt   = amt if side == "BUY" else -amt
    existing_amt = get_position_amount(sym)
    if existing_amt and existing_amt * signed_amt < 0:
        cancel_open_orders(sym)
        close_order = place_post_only_with_retries(sym,
                                                   "SELL" if existing_amt > 0 else "BUY",
                                                   abs(existing_amt))
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Closed existing position: order_id={close_order['orderId']}"
        )

    # Устанавливаем плечо
    leverage_map[sym] = leverage
    cancel_open_orders(sym)
    try:
        _client.futures_change_leverage(symbol=sym, leverage=leverage)
    except Exception as e:
        logger.warning(f"Leverage change failed: {e}")

    # Ставим ордер
    if price is not None:
        order = _client.futures_create_order(
            symbol=sym,
            side=side,
            type="LIMIT",
            timeInForce="GTC",
            price=f"{price:.8f}",
            quantity=str(amt),
        )
    else:
        order = place_post_only_with_retries(sym, side, amt)

    # Ждём и собираем fills из самого order
    fills = order.get("fills", [])
    total_qty = sum(float(f["qty"]) for f in fills)
    entry_price = (
        sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
        if total_qty else 0.0
    )
    entry_comm = sum(
        float(f["commission"]) for f in fills if f.get("commissionAsset") == "USDT"
    )

    # Берём данные позиции для маржи и ликвидации
    pos_info = _client.futures_position_information(symbol=sym)
    pos = next(x for x in pos_info if x["symbol"] == sym)
    margin_used   = float(pos.get("initialMargin", 0.0))
    liq_price     = float(pos.get("liquidationPrice", 0.0))

    # Сохраняем для закрытия
    trade_records[sym] = {
        "entry_price": entry_price,
        "entry_comm":  entry_comm
    }

    # Баланс USDT
    usdt_balance = next(
        (float(a["balance"]) for a in _client.futures_account_balance() if a["asset"] == "USDT"),
        0.0
    )

    summary = (
        f"Символ: {sym}\n"
        f"Направление: {side}\n"
        f"Количество: {signed_amt}\n"
        f"Цена входа: {entry_price}\n"
        f"Плечо: {leverage}\n"
        f"Использованная маржа: {margin_used:.8f}\n"
        f"Цена ликвидации: {liq_price}\n"
        f"Комиссия входа: {entry_comm:.8f}\n"
        f"USDT баланс: {usdt_balance:.8f}"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)


async def close_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for sym in settings.symbols:
        amt = get_position_amount(sym)
        if not amt:
            continue

        # Извлекаем записанные entry_price и entry_comm
        rec = trade_records.pop(sym, {})
        entry_price = rec.get("entry_price", 0.0)
        entry_comm  = rec.get("entry_comm",  0.0)

        # Ставим ордер на закрытие
        side = "SELL" if amt > 0 else "BUY"
        cancel_open_orders(sym)
        order = place_post_only_with_retries(sym, side, abs(amt))

        # Собираем fills для выхода
        fills = order.get("fills", [])
        total_qty = sum(float(f["qty"]) for f in fills)
        exit_price = (
            sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
            if total_qty else 0.0
        )
        exit_comm = sum(
            float(f["commission"]) for f in fills if f.get("commissionAsset") == "USDT"
        )

        total_comm = entry_comm + exit_comm
        pnl = (exit_price - entry_price) * amt - total_comm

        usdt_balance = next(
            (float(a["balance"]) for a in _client.futures_account_balance() if a["asset"] == "USDT"),
            0.0
        )

        summary = (
            f"Символ: {sym}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Цена выхода: {exit_price}\n"
            f"Общая комиссия: {total_comm:.8f}\n"
            f"Реализованный PnL: {pnl:.8f}\n"
            f"Futures USDT баланс: {usdt_balance:.8f}"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)


async def close_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for sym in settings.symbols:
        cancel_open_orders(sym)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="All open orders cancelled.")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
        f"{a['asset']}: {a['balance']}"
        for a in _client.futures_account_balance()
    ]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines) or "No balance data."
    )


if __name__ == "__main__":
    request = HTTPXRequest(
        connect_timeout=5.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=60.0
    )
    app = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .request(request)
        .build()
    )
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("create_order", create_order))
    app.add_handler(CommandHandler("active_trade", active_trade))
    app.add_handler(CommandHandler("close_trades", close_trades))
    app.add_handler(CommandHandler("close_orders", close_orders))
    app.add_handler(CommandHandler("balance", balance))
    app.run_polling()

# File: app/telegram_bot.py

import logging
import asyncio
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

# Отключаем подробные логи httpx
logging.getLogger("httpx").setLevel(logging.WARNING)

# Настройка логирования для этого модуля
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Состояния бота
webhook_lock = Lock()
webhook_paused = False

# Хранилища данных о плечах и сделках
leverage_map: dict[str, int] = {}
trade_records: dict[str, list[dict]] = {}  # symbol -> list of {"orderId","qty","price","commission"}


def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = True
    return update.message.reply_text("Webhooks processing paused.")


def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = False
    return update.message.reply_text("Webhooks processing resumed.")


async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 4 or len(args) > 5:
        return await update.message.reply_text(
            "Usage: /create_order <SYMBOL> <BUY|SELL> <AMOUNT> <LEVERAGE> [PRICE]"
        )

    symbol = args[0].upper()
    side   = args[1].upper()
    qty    = float(args[2])
    lev    = int(args[3])
    price  = float(args[4]) if len(args) == 5 else None

    # Закрываем противоположную позицию
    signed_qty   = qty if side == "BUY" else -qty
    existing_amt = get_position_amount(symbol)
    if existing_amt and existing_amt * signed_qty < 0:
        cancel_open_orders(symbol)
        opp_side = "SELL" if existing_amt > 0 else "BUY"
        close_ord = place_post_only_with_retries(symbol, opp_side, abs(existing_amt))
        await update.message.reply_text(
            f"Closed existing position: order_id={close_ord['orderId']}"
        )

    # Устанавливаем плечо
    leverage_map[symbol] = lev
    cancel_open_orders(symbol)
    try:
        _client.futures_change_leverage(symbol=symbol, leverage=lev)
    except Exception as e:
        logger.warning(f"Leverage set failed: {e}")

    # Ставим ордер
    if price is not None:
        order = _client.futures_create_order(
            symbol=symbol,
            side=side,
            type="LIMIT",
            timeInForce="GTC",
            price=f"{price:.8f}",
            quantity=str(qty),
        )
    else:
        order = place_post_only_with_retries(symbol, side, qty)

    order_id = order["orderId"]

    # Ждём, чтобы сделки появились в истории
    await asyncio.sleep(1)

    # Получаем трейды этого ордера
    all_trades   = _client.futures_account_trades(symbol=symbol)
    entry_trades = [t for t in all_trades if t["orderId"] == order_id]

    total_qty   = sum(abs(float(t["qty"])) for t in entry_trades)
    entry_price = (
        sum(float(t["price"]) * abs(float(t["qty"])) for t in entry_trades) / total_qty
        if total_qty else 0.0
    )
    entry_comm  = sum(
        float(t["commission"])
        for t in entry_trades
        if t.get("commissionAsset") == "USDT"
    )

    # DEBUG: raw fills and calculations
    logger.debug(f"[DEBUG {symbol}] entry_trades for {order_id}: {entry_trades!r}")
    logger.debug(
        f"[DEBUG {symbol}] total_qty={total_qty}, "
        f"entry_price={entry_price:.8f}, entry_comm={entry_comm:.8f}"
    )

    # Сохраняем запись
    trade_records.setdefault(symbol, []).append({
        "orderId":   order_id,
        "qty":       signed_qty,
        "price":     entry_price,
        "commission": entry_comm
    })

    # Маржа и ликвидация
    pos = next(x for x in _client.futures_position_information(symbol=symbol) if x["symbol"] == symbol)
    margin_used = float(pos.get("initialMargin", 0.0))
    liq_price   = float(pos.get("liquidationPrice", 0.0))

    # Баланс USDT
    usdt_balance = next(
        (float(a["balance"]) for a in _client.futures_account_balance() if a["asset"] == "USDT"),
        0.0
    )

    # Ответ в Telegram
    await update.message.reply_text(
        f"Символ: {symbol}\n"
        f"Направление: {side}\n"
        f"Количество: {signed_qty}\n"
        f"Цена входа: {entry_price}\n"
        f"Плечо: {lev}\n"
        f"Использованная маржа: {margin_used:.8f}\n"
        f"Цена ликвидации: {liq_price}\n"
        f"Комиссия входа: {entry_comm:.8f}\n"
        f"USDT баланс: {usdt_balance:.8f}"
    )


async def active_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = _client.futures_position_information()
    msgs = []
    for p in positions:
        amt = float(p.get("positionAmt", 0))
        if not amt:
            continue
        sym = p["symbol"]
        recs = trade_records.get(sym, [])
        total_in_qty = sum(abs(r["qty"]) for r in recs)
        entry_price = (
            sum(r["price"] * abs(r["qty"]) for r in recs) / total_in_qty
            if total_in_qty else float(p.get("entryPrice", 0.0))
        )
        entry_comm = sum(r["commission"] for r in recs)

        mark_price = float(_client.futures_mark_price(symbol=sym)["markPrice"])
        pnl_gross  = (mark_price - entry_price) * amt
        pnl_net    = pnl_gross - entry_comm

        margin_used = float(p.get("initialMargin", 0.0))
        liq_price   = float(p.get("liquidationPrice", 0.0))
        # lev         = leverage_map.get(sym, int(p.get("leverage", 1)))
        lev         = int(p.get("leverage", 1))
        side_str    = "LONG" if amt > 0 else "SHORT"

        msgs.append(
            f"Символ: {sym}\n"
            f"Направление: {side_str}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Плечо: {lev}\n"
            f"Использованная маржа: {margin_used:.8f}\n"
            f"Цена ликвидации: {liq_price}\n"
            f"PNL брутто: {pnl_gross:.8f}\n"
            f"Комиссия входа: {entry_comm:.8f}\n"
            f"PNL нетто: {pnl_net:.8f}"
        )
    await update.message.reply_text("\n\n".join(msgs) or "No active positions.")


async def close_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for symbol, recs in list(trade_records.items()):
        amt = get_position_amount(symbol)
        if not amt:
            continue

        side = "SELL" if amt > 0 else "BUY"
        cancel_open_orders(symbol)
        order = place_post_only_with_retries(symbol, side, abs(amt))
        close_id = order["orderId"]

        await asyncio.sleep(1)
        all_trades  = _client.futures_account_trades(symbol=symbol)
        exit_trades = [t for t in all_trades if t["orderId"] == close_id]

        total_exit_qty = sum(abs(float(t["qty"])) for t in exit_trades)
        exit_price     = (
            sum(float(t["price"]) * abs(float(t["qty"])) for t in exit_trades) / total_exit_qty
            if total_exit_qty else 0.0
        )
        exit_comm      = sum(
            float(t["commission"])
            for t in exit_trades
            if t.get("commissionAsset") == "USDT"
        )

        # DEBUG: raw exit fills and calculations
        logger.debug(f"[DEBUG {symbol}] exit_trades for {close_id}: {exit_trades!r}")
        logger.debug(
            f"[DEBUG {symbol}] total_exit_qty={total_exit_qty}, "
            f"exit_price={exit_price:.8f}, exit_comm={exit_comm:.8f}"
        )

        total_in_qty = sum(abs(r["qty"]) for r in recs)
        entry_price  = (
            sum(r["price"] * abs(r["qty"]) for r in recs) / total_in_qty
            if total_in_qty else 0.0
        )
        entry_comm   = sum(r["commission"] for r in recs)
        total_comm   = entry_comm + exit_comm
        pnl          = (exit_price - entry_price) * amt - total_comm

        usdt_balance = next(
            (float(a["balance"]) for a in _client.futures_account_balance() if a["asset"] == "USDT"),
            0.0
        )

        await update.message.reply_text(
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Цена выхода: {exit_price}\n"
            f"Общая комиссия: {total_comm:.8f}\n"
            f"Реализованный PnL: {pnl:.8f}\n"
            f"Futures USDT баланс: {usdt_balance:.8f}"
        )
        trade_records.pop(symbol, None)


async def close_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for sym in settings.symbols:
        cancel_open_orders(sym)
    await update.message.reply_text("All open orders cancelled.")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"{a['asset']}: {a['balance']}" for a in _client.futures_account_balance()]
    await update.message.reply_text("\n".join(lines) or "No balance data.")


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

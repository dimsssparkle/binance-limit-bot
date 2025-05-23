import logging
from threading import Lock
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
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

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

# Состояние
webhook_lock = Lock()
webhook_paused = False

# Хранилища
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


def is_entry_trade(t: dict, is_entry: bool) -> bool:
    # t['buyer'] == True for a BUY fill; False for SELL fill
    return (t.get('buyer') is True) is is_entry


async def sum_commission(symbol: str, amt: float, is_entry: bool) -> float:
    trades = sorted(
        _client.futures_account_trades(symbol=symbol),
        key=lambda x: x['time']
    )
    target = abs(amt)
    filled = 0.0
    comm = 0.0
    for t in trades:
        if t.get('commissionAsset') != 'USDT':
            continue
        if is_entry_trade(t, is_entry):
            q = abs(float(t['qty']))
            use = min(q, target - filled)
            comm += float(t['commission']) * (use / q)
            filled += use
            if filled >= target:
                break
    return comm


async def active_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = _client.futures_position_information()
    msgs = []
    for p in positions:
        amt = float(p.get('positionAmt', 0))
        if not amt:
            continue
        sym = p['symbol']
        side = 'LONG' if amt > 0 else 'SHORT'
        info = _client.futures_position_information(symbol=sym)
        pos = next(x for x in info if x['symbol'] == sym)
        lev = leverage_map.get(sym, int(pos.get('leverage', 1)))
        entry_price = float(pos.get('entryPrice', 0.0))
        margin_used = abs(amt * entry_price) / lev if lev else 0.0
        liq_price = float(pos.get('liquidationPrice', 0.0))
        mark_price = float(_client.futures_mark_price(symbol=sym)['markPrice'])
        pnl_gross = (mark_price - entry_price) * amt

        entry_comm = await sum_commission(sym, amt, is_entry=True)
        exit_comm  = await sum_commission(sym, amt, is_entry=False)
        pnl_net    = pnl_gross - entry_comm - exit_comm

        msgs.append(
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
    text = "\n\n".join(msgs) or "No active positions."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 4 or len(args) > 5:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Usage: /create_order <SYMBOL> <BUY|SELL|LONG|SHORT> <AMOUNT> <LEVERAGE> [PRICE]"
        )
        return

    sym = args[0].upper()
    side = args[1].upper()
    amt = float(args[2])
    lev = int(args[3])
    price = float(args[4]) if len(args) == 5 else None

    # закрываем противоположную позицию
    signed = amt if side in ('BUY', 'LONG') else -amt
    existing = get_position_amount(sym)
    if existing and existing * signed < 0:
        close_side = 'SELL' if existing > 0 else 'BUY'
        cancel_open_orders(sym)
        close_ord = place_post_only_with_retries(sym, close_side, abs(existing))
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Closed existing position: order_id={close_ord['orderId']}"
        )
        existing = 0.0

    # меняем плечо и отменяем старые ордера
    leverage_map[sym] = lev
    cancel_open_orders(sym)
    try:
        _client.futures_change_leverage(symbol=sym, leverage=lev)
    except Exception as e:
        logger.warning(f"Leverage change failed: {e}")

    # выставляем ордер
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

    # собираем данные о трейде
    # --- entry_price по сделкам
    trades = sorted(_client.futures_account_trades(symbol=sym), key=lambda x: x['time'])
    target = abs(signed)
    filled = 0.0
    cost = 0.0
    for t in trades:
        if t.get('commissionAsset') != 'USDT': continue
        if is_entry_trade(t, True):
            q = abs(float(t['qty']))
            use = min(q, target - filled)
            cost += float(t['price']) * use
            filled += use
            if filled >= target: break
    entry_price = cost / filled if filled else 0.0

    entry_comm = await sum_commission(sym, signed, is_entry=True)

    # позиция и маржа
    info = _client.futures_position_information(symbol=sym)
    pos = next(x for x in info if x['symbol'] == sym)
    liq_price = float(pos.get('liquidationPrice', 0.0))
    margin_used = abs(signed * entry_price) / lev if lev else 0.0

    # сохраняем для close_trades
    trade_records[sym] = {
        "entry_price": entry_price,
        "entry_comm": entry_comm
    }

    # баланс
    usdt_balance = next(
        (float(a['balance']) for a in _client.futures_account_balance() if a['asset'] == 'USDT'),
        0.0
    )

    summary = (
        f"Символ: {sym}\n"
        f"Направление: {side}\n"
        f"Количество: {signed}\n"
        f"Цена входа: {entry_price}\n"
        f"Плечо: {lev}\n"
        f"Использованная маржа: {margin_used:.8f}\n"
        f"Цена ликвидации: {liq_price}\n"
        f"Комиссия входа: {entry_comm:.8f}\n"
        f"USDT баланс: {usdt_balance:.8f}"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)


async def close_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for sym in settings.symbols:
        amt = get_position_amount(sym)
        if not amt: continue

        record = trade_records.pop(sym, {})
        ep = record.get("entry_price", 0.0)
        ec = record.get("entry_comm",  0.0)

        side = "SELL" if amt > 0 else "BUY"
        cancel_open_orders(sym)
        order = place_post_only_with_retries(sym, side, abs(amt))

        # exit_price
        fills = order.get("fills", []) or []
        if fills:
            qty = sum(float(f["qty"]) for f in fills)
            exit_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / qty
        else:
            exit_price = float(order.get("avgPrice", 0.0))

        ec_exit = await sum_commission(sym, amt, is_entry=False)
        total_comm = ec + ec_exit

        pnl = (exit_price - ep) * amt - total_comm

        usdt_balance = next(
            (float(a['balance']) for a in _client.futures_account_balance() if a['asset'] == 'USDT'),
            0.0
        )

        summary = (
            f"Символ: {sym}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {ep}\n"
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
    lines = [f"{a['asset']}: {a['balance']}" for a in _client.futures_account_balance()]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(lines) or "No balance data.")


if __name__ == '__main__':
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
    app.add_handler(CommandHandler('pause', pause))
    app.add_handler(CommandHandler('resume', resume))
    app.add_handler(CommandHandler('balance', balance))
    app.add_handler(CommandHandler('create_order', create_order))
    app.add_handler(CommandHandler('active_trade', active_trade))
    app.add_handler(CommandHandler('close_trades', close_trades))
    app.add_handler(CommandHandler('close_orders', close_orders))
    app.run_polling()

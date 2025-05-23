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

# Устанавливаем уровень логирования для httpx
logging.getLogger("httpx").setLevel(logging.WARNING)

# Общий логгер
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

# Флаги и хранилище
webhook_lock = Lock()
webhook_paused = False
leverage_map: dict[str, int] = {}
# trade_records хранит суммарные данные по входу: общий объём, сумма стоимости, сумма входных комиссий
trade_records: dict[str, dict] = {}


def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = True
    return update.message.reply_text("Webhooks processing paused.")


def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = False
    return update.message.reply_text("Webhooks processing resumed.")


def is_entry_trade(t: dict, is_entry: bool) -> bool:
    return (t.get('buyer') is True) is is_entry


async def sum_commission(symbol: str, amt: float, is_entry: bool) -> float:
    trades = sorted(_client.futures_account_trades(symbol=symbol), key=lambda t: t['time'])
    target_qty = abs(amt)
    filled = 0.0
    comm = 0.0
    for t in trades:
        if t.get('commissionAsset') != 'USDT':
            continue
        if is_entry_trade(t, is_entry):
            trade_qty = abs(float(t.get('qty', 0)))
            qty_to_count = min(trade_qty, target_qty - filled)
            comm += float(t.get('commission', 0)) * (qty_to_count / trade_qty)
            filled += qty_to_count
            if filled >= target_qty:
                break
    return comm


async def active_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = _client.futures_position_information()
    messages = []
    for p in positions:
        amt = float(p.get('positionAmt', 0))
        if amt == 0:
            continue
        symbol = p['symbol']
        side = 'LONG' if amt > 0 else 'SHORT'
        info = _client.futures_position_information(symbol=symbol)
        pos = next((x for x in info if x['symbol'] == symbol), {})
        leverage = leverage_map.get(symbol, int(pos.get('leverage', 1)))
        entry_price = float(pos.get('entryPrice', 0))
        margin_used = float(pos.get('initialMargin', abs(amt * entry_price) / leverage))
        liquidation_price = float(pos.get('liquidationPrice', 0))
        mark_price = float(_client.futures_mark_price(symbol=symbol).get('markPrice', 0))
        pnl_gross = (mark_price - entry_price) * amt

        entry_comm = await sum_commission(symbol, amt, is_entry=True)
        exit_comm = await sum_commission(symbol, amt, is_entry=False)
        pnl_net = pnl_gross - entry_comm - exit_comm

        msg = (
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Плечо: {leverage}\n"
            f"Использованная маржа: {margin_used:.8f}\n"
            f"Цена ликвидации: {liquidation_price}\n"
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

    symbol = args[0].upper()
    side = args[1].upper()
    amt = float(args[2])
    leverage = int(args[3])
    price = float(args[4]) if len(args) == 5 else None

    signed_amt = amt if side == 'BUY' else -amt
    existing_amt = get_position_amount(symbol)
    if existing_amt and existing_amt * signed_amt < 0:
        # закрываем противоположную позицию
        close_side = 'SELL' if existing_amt > 0 else 'BUY'
        cancel_open_orders(symbol)
        place_post_only_with_retries(symbol, close_side, abs(existing_amt))
        existing_amt = 0.0

    leverage_map[symbol] = leverage
    cancel_open_orders(symbol)
    try:
        _client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        logger.warning(f"Leverage change failed: {e}")

    # выставляем основной ордер
    order = place_post_only_with_retries(symbol, side, amt, price)
    fills = order.get('fills', []) or []
    if fills:
        total_qty = sum(float(f['qty']) for f in fills)
        entry_price = sum(float(f['price']) * float(f['qty']) for f in fills) / total_qty
    else:
        entry_price = float(order.get('avgPrice', price or 0.0))

    # вычисляем комиссию входа
    entry_comm = await sum_commission(symbol, signed_amt, is_entry=True)

    # сохраняем данные для последующего закрытия
    record = trade_records.get(symbol, {
        'total_qty': 0.0,
        'entry_value_sum': 0.0,
        'entry_comm': 0.0
    })
    record['total_qty'] += signed_amt
    record['entry_value_sum'] += signed_amt * entry_price
    record['entry_comm'] += entry_comm
    trade_records[symbol] = record

    # сообщение в телеграм
    margin_used = abs(signed_amt * entry_price) / leverage if leverage else 0.0
    liq_price = float(_client.futures_position_information(symbol=symbol)[0].get('liquidationPrice', 0))
    summary = (
        f"Символ: {symbol}\n"
        f"Направление: {side}\n"
        f"Количество: {signed_amt}\n"
        f"Цена входа: {entry_price}\n"
        f"Плечо: {leverage}\n"
        f"Использованная маржа: {margin_used:.8f}\n"
        f"Цена ликвидации: {liq_price}\n"
        f"Комиссия входа: {entry_comm:.8f}\n"
        f"Futures USDT баланс: {next((float(a['balance']) for a in _client.futures_account_balance() if a['asset']=='USDT'),0.0):.8f}"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)


async def close_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for sym in settings.symbols:
        amt = get_position_amount(sym)
        if not amt:
            continue

        # получаем накопленные записи по входу
        record = trade_records.pop(sym, None)
        if record:
            total_qty = record['total_qty']
            entry_price = record['entry_value_sum'] / total_qty if total_qty else 0.0
            entry_comm = record['entry_comm']
        else:
            total_qty = amt
            entry_price = 0.0
            entry_comm = 0.0

        # закрываем позицию
        side = 'SELL' if amt > 0 else 'BUY'
        cancel_open_orders(sym)
        order = place_post_only_with_retries(sym, side, abs(amt))
        fills = order.get('fills', []) or []
        if fills:
            total_exit_qty = sum(float(f['qty']) for f in fills)
            exit_price = sum(float(f['price']) * float(f['qty']) for f in fills) / total_exit_qty
        else:
            exit_price = float(order.get('avgPrice', 0.0))

        # комиссия выхода
        exit_comm = await sum_commission(sym, abs(amt), is_entry=False)
        total_comm = entry_comm + exit_comm

        # расчёт PnL
        pnl_gross = (exit_price - entry_price) * amt
        pnl_net = pnl_gross - total_comm

        # баланс
        usdt_balance = next((float(a['balance']) for a in _client.futures_account_balance() if a['asset']=='USDT'), 0.0)

        summary = (
            f"Символ: {sym}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price:.2f}\n"
            f"Цена выхода: {exit_price:.2f}\n"
            f"Общая комиссия: {total_comm:.8f}\n"
            f"Реализованный PnL: {pnl_net:.8f}\n"
            f"Futures USDT баланс: {usdt_balance:.8f}"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)


async def close_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for sym in settings.symbols:
        cancel_open_orders(sym)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="All open orders cancelled.")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _client.futures_account_balance()
    lines = [f"{a['asset']}: {a['balance']}" for a in account]
    text = "\n".join(lines) if lines else "No balance data."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


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
    app.add_handler(CommandHandler('close_trades', close_trades))
    app.add_handler(CommandHandler('close_orders', close_orders))
    app.add_handler(CommandHandler('active_trade', active_trade))
    app.add_handler(CommandHandler('create_order', create_order))
    app.run_polling()

import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from app.config import settings
from app.binance_client import (
    get_position_amount,
    cancel_open_orders,
    place_post_only_with_retries,
    _client,
)
from threading import Lock

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO)
)
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

# Control for webhooks processing
webhook_lock = Lock()
webhook_paused = False

# /close_trades - close all open positions
async def close_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = []
    for sym in settings.symbols:
        amt = get_position_amount(sym)
        if amt:
            side = 'SELL' if amt > 0 else 'BUY'
            order = place_post_only_with_retries(sym, side, abs(amt))
            results.append(f"{sym}: closed_order_id={order.get('orderId')}")
    text = "\n".join(results) if results else "No positions to close."
    await update.message.reply_text(text)

# /close_orders - cancel all open orders
async def close_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for sym in settings.symbols:
        cancel_open_orders(sym)
    await update.message.reply_text("All open orders cancelled.")

# /balance - futures account balance
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _client.futures_account_balance()
    lines = [f"{a['asset']}: {a['balance']}" for a in account]
    text = "\n".join(lines) if lines else "No balance data."
    await update.message.reply_text(text)

# /active_trade - detailed open positions (exit commission simplified)
async def active_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = _client.futures_position_information()

    args = context.args
    try:
        leverage = int(args[0]) if args and args[0].isdigit() else settings.default_leverage
    except Exception:
        leverage = settings.default_leverage

    for p in positions:
        amt = float(p.get('positionAmt', 0))
        if amt == 0:
            continue
        symbol = p['symbol']
        side = 'LONG' if amt > 0 else 'SHORT'
        entry_price = float(p.get('entryPrice', 0))
        margin_used = abs(amt * entry_price) / leverage if leverage else 0.0
        liquidation_price = float(p.get('liquidationPrice', 0))
        mark_price = float(_client.futures_mark_price(symbol=symbol).get('markPrice', 0))
        pnl_gross = (mark_price - entry_price) * amt

        trades = sorted(_client.futures_account_trades(symbol=symbol), key=lambda t: t['time'])

        def sum_commission(is_entry: bool):
            target_qty = abs(amt)
            filled = 0.0
            comm = 0.0
            for t in trades:
                if t.get('commissionAsset') != 'USDT':
                    continue
                if (t.get('buyer') == (amt > 0)) is is_entry:
                    trade_qty = abs(float(t.get('qty', 0)))
                    qty_to_count = min(trade_qty, target_qty - filled)
                    comm += float(t.get('commission', 0)) * (qty_to_count / trade_qty)
                    filled += qty_to_count
                    if filled >= target_qty:
                        break
            return comm

        entry_comm = sum_commission(is_entry=True)
        exit_comm = entry_comm
        pnl_net = pnl_gross - entry_comm - exit_comm

        msg = (
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Плечо: {leverage}\n"
            f"Использованная маржа: {margin_used:.8f}\n"
            f"Цена ликвидации: {liquidation_price}\n"
            f"Комиссия входа: {entry_comm:.8f}\n"
            f"Gross комиссия выхода: {exit_comm:.8f}\n"
            f"PnL брутто: {pnl_gross:.8f}\n"
            f"PnL нетто: {pnl_net:.8f}"
        )
        await update.message.reply_text(msg)

# /create_order - open new position and output summary
async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    # expect SYMBOL, SIDE, AMOUNT, optional LEVERAGE, optional PRICE
    if len(args) < 3 or len(args) > 5:
        await update.message.reply_text(
            "Usage: /create_order <SYMBOL> <BUY|SELL|LONG|SHORT> <AMOUNT> [LEVERAGE] [PRICE]"
        )
        return
    symbol, side, amt_str = args[0:3]
    # determine if next arg is leverage or price or both
    entry_price = None
    leverage = settings.default_leverage
    if len(args) >= 4:
        # if fourth arg is integer leverage
        if args[3].isdigit():
            leverage = int(args[3])
            if len(args) == 5:
                entry_price = float(args[4])
        else:
            entry_price = float(args[3])
    try:
        amt = float(amt_str) * (1 if side.upper() in ('BUY', 'LONG') else -1)
    except ValueError:
        await update.message.reply_text("Invalid amount. Please enter numeric values.")
        return

    # Place order (market if price not provided)
    order = None
    if entry_price is not None:
        order = place_post_only_with_retries(symbol, side.upper(), abs(amt), price=entry_price)
    else:
        order = place_post_only_with_retries(symbol, side.upper(), abs(amt))

    # Calculate fields
    used_price = entry_price or float(order.get('avgPrice', 0))
    margin_used = abs(amt * used_price) / leverage if leverage else 0.0
    pos_info = _client.futures_position_information(symbol=symbol)[0]
    liquidation_price = float(pos_info.get('liquidationPrice', 0))
    entry_comm = float(order.get('cummulativeQuoteQty', 0)) * settings.commission_rate
    exit_comm = entry_comm

    msg = (
        f"Символ: {symbol}"
        f"Направление: {side.upper()}"
        f"Количество: {amt}"
        f"Цена входа: {used_price}"
        f"Плечо: {leverage}"
        f"Использованная маржа: {margin_used:.8f}"
        f"Цена ликвидации: {liquidation_price}"
        f"Комиссия входа: {entry_comm:.8f}"
        f"Gross комиссия выхода: {exit_comm:.8f}"
    )
    await update.message.reply_text(msg)

# Bot setup
app = ApplicationBuilder().token(settings.telegram_token).build()
handlers = [
    ('close_trades', close_trades),
    ('close_orders', close_orders),
    ('balance', balance),
    ('active_trade', active_trade),  # detailed status
    ('active_trades', active_trade),
    ('create_order', create_order),
    ('pause', pause),
    ('resume', resume),
]
for cmd, func in handlers:
    app.add_handler(CommandHandler(cmd, func))

if __name__ == '__main__':
    logger.info("Starting Telegram bot...")
    app.run_polling()

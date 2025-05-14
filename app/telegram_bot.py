import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from app.config import settings
from app.binance_client import get_position_amount, cancel_open_orders, place_post_only_with_retries
from threading import Lock

# Configure logging
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
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
    from app.binance_client import _client
    account = _client.futures_account_balance()
    lines = [f"{a['asset']}: {a['balance']}" for a in account]
    text = "\n".join(lines) if lines else "No balance data."
    await update.message.reply_text(text)

# /active_trade - detailed open positions (exit commission simplified)
async def active_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from app.binance_client import _client
    positions = _client.futures_position_information()
    messages = []

    # Leverage from args or default
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

        # Fetch raw trades
        trades = sorted(_client.futures_account_trades(symbol=symbol), key=lambda t: t['time'])
        logger.info(f"Raw trades for {symbol}: {trades}")

        # Helper to sum commission for first matching trades up to target qty
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
                    if trade_qty > 0:
                        comm += float(t.get('commission', 0)) * (qty_to_count / trade_qty)
                    filled += qty_to_count
                    if filled >= target_qty:
                        break
            return comm

        # Entry commission summed normally
        entry_comm = sum_commission(is_entry=True)
        # For simplicity, exit commission set equal to total entry commission
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
        messages.append(msg)

    reply = "\n---\n".join(messages) if messages else "No active positions."
    await update.message.reply_text(reply)

# /resume - resume webhook handling
async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = False
    await update.message.reply_text("Webhooks resumed.")

# /pause - pause webhook handling
async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webhook_paused
    webhook_paused = True
    await update.message.reply_text("Webhooks paused.")

# /create_order - create default order
async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sym = settings.default_symbol
    args = context.args
    side = args[0].upper() if len(args) > 0 else 'BUY'
    leverage = int(args[1]) if len(args) > 1 and args[1].isdigit() else settings.default_leverage
    qty = float(args[2]) if len(args) > 2 else settings.default_quantity
    from app.binance_client import _client, get_position_amount

    # Ensure leverage
    try:
        _client.futures_change_leverage(symbol=sym, leverage=leverage)
        settings.default_leverage = leverage
        logger.info(f"Leverage set to {leverage}x for {sym}")
    except Exception as e:
        logger.error(f"Failed to set leverage: {e}")

    # Place order
    order = place_post_only_with_retries(sym, side, qty)

    # After order, fetch updated position
    positions = _client.futures_position_information()
    # Find our symbol
    pos = next((p for p in positions if p['symbol'] == sym and float(p.get('positionAmt',0)) != 0), None)
    if pos:
        # If position existed before, show full active_trade output
        await active_trade(update, context)
    else:
        # New position, show minimal create msg
        amt = float(pos.get('positionAmt')) if pos else (qty if side=='BUY' else -qty)
        entry_price = float(pos.get('entryPrice')) if pos else float(order.get('price', 0))
        margin_used = abs(amt * entry_price) / leverage if leverage else 0.0
        liquidation_price = float(pos.get('liquidationPrice', 0))
        # Commission entry / exit set equal for simplicity
        entry_comm = 0.0
        exit_comm = 0.0

        msg = (
            f"Символ: {sym}"
            f"Направление: {side}"
            f"Количество: {amt}"
            f"Цена входа: {entry_price}"
            f"Плечо: {leverage}"
            f"Использованная маржа: {margin_used:.8f}"
            f"Цена ликвидации: {liquidation_price}"
            f"Комиссия входа: {entry_comm:.8f}"
            f"Gross комиссия выхода: {exit_comm:.8f}"
        )
        await update.message.reply_text(msg)

# Bot setup
app = ApplicationBuilder().token(settings.telegram_token).build()().token(settings.telegram_token).build()
handlers = [
    ('close_trades', close_trades),
    ('close_orders', close_orders),
    ('balance', balance),
    ('active_trade', active_trade),
    ('active_trades', active_trade),  # alias
    ('resume', resume),
    ('pause', pause),
    ('create_order', create_order),
]
for cmd, func in handlers:
    app.add_handler(CommandHandler(cmd, func))

if __name__ == '__main__':
    logger.info("Starting Telegram bot...")
    app.run_polling()


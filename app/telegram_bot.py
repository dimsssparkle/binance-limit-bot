import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from app.config import settings
from app.binance_client import get_position_amount, cancel_open_orders, place_post_only_with_retries
from app.handlers import handle_signal
from threading import Lock

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

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

# /active_trade - detailed open positions
async def active_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from app.binance_client import _client
    positions = _client.futures_position_information()
    messages = []
    for p in positions:
        amt = float(p.get('positionAmt', 0))
        if amt == 0:
            continue
        symbol = p['symbol']
        side = 'LONG' if amt > 0 else 'SHORT'
        entry_price = float(p.get('entryPrice', 0))
        leverage = int(p.get('leverage', 1))
        # Фактически использованная маржа
        margin_used = abs(amt * entry_price) / leverage if leverage else 0.0
        # Цена ликвидации
        liquidation_price = float(p.get('liquidationPrice', 0))
        # Маркерная цена для расчёта PnL
        mark_data = _client.futures_mark_price(symbol=symbol)
        mark_price = float(mark_data.get('markPrice', 0))
        # Расчёт брутто-PnL
        pnl_gross = (mark_price - entry_price) * amt
        # Комиссия входа (точная из API)
        trades = _client.futures_account_trades(symbol=symbol)
        entry_comm = 0.0
        exit_comm = 0.0
        for t in trades:
            if float(t['qty']) == abs(amt) and t['isBuyer'] == (amt > 0):
                entry_comm += float(t.get('commission', 0))
            if float(t['qty']) == abs(amt) and t['isBuyer'] != (amt > 0):
                exit_comm += float(t.get('commission', 0))
        # Чистый PnL с учётом комиссий
        pnl_net = pnl_gross - entry_comm - exit_comm
        msg = (
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Плечо: {leverage}\n"
            f"Использованная маржа: {margin_used:.8f}\n"
            f"Цена ликвидации: {liquidation_price}\n"
            f"Комиссия входа: {entry_comm}\n"
            f"Gross комиссия выхода: {exit_comm}\n"
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
    qty = settings.default_quantity
    args = context.args
    side = args[0].upper() if args else 'BUY'
    order = place_post_only_with_retries(sym, side, qty)
    await update.message.reply_text(f"Created {side} order {order.get('orderId')} for {sym} x{qty}")

# Build and run the bot
app = ApplicationBuilder().token(settings.telegram_token).build()
app.add_handler(CommandHandler('close_trades', close_trades))
app.add_handler(CommandHandler('close_orders', close_orders))
app.add_handler(CommandHandler('balance', balance))
app.add_handler(CommandHandler('active_trade', active_trade))
app.add_handler(CommandHandler('resume', resume))
app.add_handler(CommandHandler('pause', pause))
app.add_handler(CommandHandler('create_order', create_order))

if __name__ == '__main__':
    logger.info("Starting Telegram bot...")
    app.run_polling()

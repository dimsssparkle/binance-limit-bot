import logging
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from app.config import settings
from app.binance_client import (
    get_position_amount, cancel_open_orders, place_post_only_with_retries
)
from app.handlers import handle_signal
from threading import Lock

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

# Telegram Bot Initialization
bot = Bot(token=settings.telegram_token)
updater = Updater(bot=bot, use_context=True)
dispatcher = updater.dispatcher

# Control for webhooks processing
webhook_lock = Lock()
webhook_paused = False

# /close_trades - close all open positions
def close_trades(update: Update, context: CallbackContext):
    symbols = settings.symbols
    results = []
    for sym in symbols:
        amt = get_position_amount(sym)
        if amt:
            side = 'SELL' if amt > 0 else 'BUY'
            order = place_post_only_with_retries(sym, side, abs(amt))
            results.append(f"{sym}: closed_order_id={order.get('orderId')}")
    text = "\n".join(results) if results else "No positions to close."
    update.message.reply_text(text)

# /close_orders - cancel all open orders
def close_orders(update: Update, context: CallbackContext):
    for sym in settings.symbols:
        from app.binance_client import cancel_open_orders as _cancel
        _cancel(sym)
    update.message.reply_text("All open orders cancelled.")

# /balance - futures account balance
def balance(update: Update, context: CallbackContext):
    client = Bot(token=settings.telegram_token)
    account = client.get_futures_account()  # implement via binance client if needed
    balances = {item['asset']: item['balance'] for item in account.get('assets', [])}
    text = "\n".join(f"{asset}: {balance}" for asset, balance in balances.items())
    update.message.reply_text(text or "No balance data.")

# /active_trade - detailed open positions
def active_trade(update: Update, context: CallbackContext):
    client = Bot(token=settings.telegram_token)
    positions = client.get_futures_account()['positions']  # or use binance client futures_position_information
    messages = []
    for p in positions:
        amt = float(p.get('positionAmt', 0))
        if amt == 0:
            continue
        symbol = p['symbol']
        side = 'LONG' if amt > 0 else 'SHORT'
        entry_price = float(p.get('entryPrice', 0))
        leverage = int(p.get('leverage', 1))
        margin_used = abs(amt * entry_price) / leverage if leverage else 0.0
        liquidation_price = float(p.get('liquidationPrice', 0))
        entry_fee = 0.0
        gross_exit_fee = entry_fee
        pnl_est = entry_price + entry_fee + gross_exit_fee
        msg = (
            f"Символ: {symbol}\n"
            f"Направление: {side}\n"
            f"Количество: {amt}\n"
            f"Цена входа: {entry_price}\n"
            f"Плечо: {leverage}\n"
            f"Использованная маржа: {margin_used:.6f}\n"
            f"Цена ликвидации: {liquidation_price}\n"
            f"Комиссия входа: {entry_fee}\n"
            f"Gross комиссия выхода: {gross_exit_fee}\n"
            f"Ориентировочный PnL: {pnl_est}"
        )
        messages.append(msg)
    reply = "\n---\n".join(messages) if messages else 'No active positions.'
    update.message.reply_text(reply)

# /resume - resume webhook handling
def resume(update: Update, context: CallbackContext):
    global webhook_paused
    webhook_paused = False
    update.message.reply_text("Webhooks resumed.")

# /pause - pause webhook handling
def pause(update: Update, context: CallbackContext):
    global webhook_paused
    webhook_paused = True
    update.message.reply_text("Webhooks paused.")

# /create_order - create default order
def create_order(update: Update, context: CallbackContext):
    sym = settings.default_symbol
    qty = settings.default_quantity
    side = context.args[0].upper() if context.args else 'BUY'
    order = place_post_only_with_retries(sym, side, qty)
    update.message.reply_text(f"Created {side} order {order.get('orderId')} for {sym} x{qty}")

# Register handlers
dispatcher.add_handler(CommandHandler('close_trades', close_trades))
dispatcher.add_handler(CommandHandler('close_orders', close_orders))
dispatcher.add_handler(CommandHandler('balance', balance))
dispatcher.add_handler(CommandHandler('active_trade', active_trade))
dispatcher.add_handler(CommandHandler('resume', resume))
dispatcher.add_handler(CommandHandler('pause', pause))
dispatcher.add_handler(CommandHandler('create_order', create_order))

if __name__ == '__main__':
    updater.start_polling()
    logger.info("Telegram bot started")
    updater.idle()

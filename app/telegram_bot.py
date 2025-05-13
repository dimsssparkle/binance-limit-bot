import logging
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from app.config import settings
from app.binance_client import (
    get_position_amount, cancel_open_orders, get_current_book, place_post_only_with_retries
)
from app.handlers import handle_signal
from threading import Lock

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

# Telegram Bot Initialization
bot = Bot(token=settings.telegram_token)
updater = Updater(bot=bot, use_context=True)
dispatcher = updater.dispatcher

# Lock to pause/resume webhooks
webhook_lock = Lock()
webhook_paused = False

# Command: /close_trades
def close_trades(update: Update, context: CallbackContext):
    """Закрыть все текущие позиции"""
    symbols = settings.symbols  # список символов из конфига
    results = []
    for sym in symbols:
        amt = get_position_amount(sym)
        if amt != 0:
            side = 'SELL' if amt > 0 else 'BUY'
            order = place_post_only_with_retries(sym, side, abs(amt))
            results.append(f"{sym}: closed {order['orderId']}")
    update.message.reply_text("\n".join(results) if results else "No positions to close.")

# Command: /close_orders
def close_orders(update: Update, context: CallbackContext):
    """Отменить все текущие ордера"""
    symbols = settings.symbols
    for sym in symbols:
        cancel_open_orders(sym)
    update.message.reply_text("All open orders cancelled.")

# Command: /balance
def balance(update: Update, context: CallbackContext):
    """Показать фьючерсный баланс"""
    account = bot.get_futures_account()  # placeholder, implement via client
    balances = {item['asset']: item['balance'] for item in account['assets']}
    text = '\n'.join(f"{k}: {v}" for k, v in balances.items())
    update.message.reply_text(text)

# Command: /active_trade
def active_trade(update: Update, context: CallbackContext):
    """Получить детальную информацию по текущим открытым позициям"""
    # Используем прямой доступ к Binance Client для получения подробностей
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
        # Расчет маржи
        margin_used = abs(amt * entry_price) / leverage if leverage else 0.0
        liquidation_price = float(p.get('liquidationPrice', 0))
        # Комиссия при входе (ставим заглушку 0 или можно получить через историю сделок)
        entry_fee = 0.0
        gross_exit_fee = entry_fee
        # Ориентировочный PnL = break_even_price + комиссии
        pnl_est = entry_price + entry_fee + gross_exit_fee
        msg = (
            f"Символ: {symbol}"
            f"Направление: {side}"
            f"Количество: {amt}"
            f"Цена входа: {entry_price}"
            f"Плечо: {leverage}"
            f"Использованная маржа: {margin_used:.6f}"
            f"Цена ликвидации: {liquidation_price}"
            f"Комиссия входа: {entry_fee}"
            f"Gross комиссия выхода: {gross_exit_fee}"
            f"Ориентировочный PnL: {pnl_est}"
        )
        messages.append(msg)
    reply = '---'.join(messages) if messages else 'No active positions.'
    update.message.reply_text(reply)
:
    """Получить текущие открытые позиции"""
    symbols = settings.symbols
    results = []
    for sym in symbols:
        amt = get_position_amount(sym)
        if amt != 0:
            results.append(f"{sym}: {amt}")
    update.message.reply_text("\n".join(results) if results else "No active positions.")

# Command: /resume
def resume(update: Update, context: CallbackContext):
    """Возобновить обработку вебхуков"""
    global webhook_paused
    webhook_paused = False
    update.message.reply_text("Webhooks resumed.")

# Command: /pause
def pause(update: Update, context: CallbackContext):
    """Приостановить обработку вебхуков"""
    global webhook_paused
    webhook_paused = True
    update.message.reply_text("Webhooks paused.")

# Command: /create_order
def create_order(update: Update, context: CallbackContext):
    """Создать ордер по умолчанию"""
    sym = settings.default_symbol
    qty = settings.default_quantity
    side = context.args[0].upper() if context.args else 'BUY'
    order = place_post_only_with_retries(sym, side, qty)
    update.message.reply_text(f"Created {side} order {order['orderId']} for {sym} x{qty}")

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

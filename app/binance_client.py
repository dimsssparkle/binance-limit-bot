"""
app/binance_client.py

Обёртка над Binance Futures API.
Содержит методы для выставления Post-Only ордеров на вход и на выход с учётом смещения цены на тик.
"""

import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

# Инициализируем клиента один раз
_client = Client(settings.binance_api_key, settings.binance_api_secret)

def get_price_filter(symbol: str) -> dict:
    """
    Получить PRICE_FILTER из exchangeInfo для заданного символа.
    В фильтре есть minPrice, maxPrice, tickSize и другие параметры.
    """
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return f
    raise ValueError(f"No PRICE_FILTER for symbol {symbol}")

def calculate_entry_price(symbol: str, side: str) -> float:
    """
    Рассчитать цену входа:
    - Для BUY: best_bid - tickSize
    - Для SELL: best_ask + tickSize
    """
    pf = get_price_filter(symbol)
    tick_size = float(pf["tickSize"])
    book = _client.futures_order_book(symbol=symbol, limit=5)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])

    if side.upper() == "BUY":
        raw = best_bid - tick_size
    else:
        raw = best_ask + tick_size

    # Форматируем по precision tickSize
    decimals = abs(int(round(math.log10(tick_size))))
    return float(f"{raw:.{decimals}f}")

def calculate_exit_price(symbol: str, side: str) -> float:
    """
    Рассчитать цену выхода, то есть зеркально входу:
    - Если позиция была BUY, закрываем SELL по (best_ask + tickSize)
    - Если позиция была SELL, закрываем BUY по (best_bid - tickSize)
    """
    # Для выхода нужно противоположное направление
    opposite = "SELL" if side.upper() == "BUY" else "BUY"
    return calculate_entry_price(symbol, opposite)

def place_post_only_smart_order(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only LIMIT-ордер на вход или выход.
    Если side = исходная сторона позиции, то это вход.
    Если side = 'close', то в handlers передаётся исходный side и сюда приходит 'close',
    но лучше различать в handlers.
    """
    # Здесь предполагаем, что `side` — это реальный side ордера.
    price = calculate_entry_price(symbol, side)
    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTX",  # Post Only
        "price": str(price),
        "quantity": str(quantity),
    }
    try:
        return _client.futures_create_order(**params)
    except BinanceAPIException:
        raise

def place_post_only_smart_exit(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only LIMIT-ордер на закрытие позиции:
    - вычисляем exit_price
    - выставляем SELL если позиция BUY и наоборот
    """
    exit_price = calculate_exit_price(symbol, side)
    close_side = "SELL" if side.upper() == "BUY" else "BUY"
    pf = get_price_filter(symbol)
    # формат exit_price уже сделан в calculate_exit_price
    price_str = str(exit_price)

    params = {
        "symbol": symbol,
        "side": close_side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "price": price_str,
        "quantity": str(quantity),
    }
    try:
        return _client.futures_create_order(**params)
    except BinanceAPIException:
        raise

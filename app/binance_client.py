"""
app/binance_client.py

Обёртка над Binance Futures API.
Содержит методы для выставления Post-Only ордеров и получения текущей позиции.
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
    В фильтре содержатся minPrice, maxPrice, tickSize и другие параметры.
    """
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return f
    raise ValueError(f"Нет PRICE_FILTER для символа {symbol}")


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

    decimals = abs(int(round(math.log10(tick_size))))
    return float(f"{raw:.{decimals}f}")


def calculate_exit_price(symbol: str, side: str) -> float:
    """
    Рассчитать цену выхода на противоположном side:
    - Для закрытия BUY позиции: SELL по best_ask + tickSize
    - Для закрытия SELL позиции: BUY по best_bid - tickSize
    """
    opposite = "SELL" if side.upper() == "BUY" else "BUY"
    return calculate_entry_price(symbol, opposite)


def place_post_only_order(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only LIMIT ордер на вход позиции.
    """
    price = calculate_entry_price(symbol, side)
    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "price": str(price),
        "quantity": str(quantity),
    }
    try:
        return _client.futures_create_order(**params)
    except BinanceAPIException:
        raise


def place_post_only_exit(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only LIMIT ордер на закрытие позиции.
    """
    exit_price = calculate_exit_price(symbol, side)
    close_side = "SELL" if side.upper() == "BUY" else "BUY"
    params = {
        "symbol": symbol,
        "side": close_side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "price": str(exit_price),
        "quantity": str(quantity),
    }
    try:
        return _client.futures_create_order(**params)
    except BinanceAPIException:
        raise


def get_position_amount(symbol: str) -> float:
    """
    Получить текущий объём позиции для заданного символа:
    возвращает положительное значение для long, отрицательное для short.
    """
    positions = _client.futures_position_information()
    for p in positions:
        if p["symbol"] == symbol:
            return float(p.get("positionAmt", 0))
    return 0.0
"""
app/binance_client.py

Обёртка над Binance Futures API.
Метод для выставления Post-Only лимитного ордера.
"""

import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

# Инициализируем клиента один раз
_client = Client(settings.binance_api_key, settings.binance_api_secret)


def get_price_filter(symbol: str) -> dict:
    """
    Получение PRICE_FILTER из exchangeInfo для символа:
    - minPrice, maxPrice, tickSize и т.д.
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
    Рассчитывает цену входа:
    - Берёт best bid/ask из стакана.
    - Для BUY: price = best_bid - tickSize.
    - Для SELL: price = best_ask + tickSize.

    :return: float, скорректированная цена
    """
    pf = get_price_filter(symbol)
    tick_size = float(pf["tickSize"])
    book = _client.futures_order_book(symbol=symbol, limit=5)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])

    if side.upper() == "BUY":
        price = best_bid - tick_size
    else:
        price = best_ask + tick_size

    # Форматируем по decimal точности тикета
    decimals = abs(int(round(math.log10(tick_size))))
    return float(f"{price:.{decimals}f}")


def place_post_only_order(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставляет Post-Only LIMIT-ордер по вычисленной цене входа:
    - Вычисляет цену через calculate_entry_price.
    - Выставляет ордер с timeInForce="GTX".
    """
    price = calculate_entry_price(symbol, side)
    price_str = str(price)

    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTX",  # Post Only
        "price": price_str,
        "quantity": str(quantity),
    }

    try:
        return _client.futures_create_order(**params)
    except BinanceAPIException:
        # Ошибки прокидываем наружу
        raise
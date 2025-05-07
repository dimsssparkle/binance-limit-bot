# app/binance_client.py

"""
app/binance_client.py

Обёртка над Binance Futures API:
- Вычисление цены maker (Post-Only).
- Выставление maker-ордеров на вход и выход.
- Отмена висящих ордеров.
- Ожидание фактического исполнения (polling).
- Получение текущей позиции.
"""

import math
import time
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

_client = Client(settings.binance_api_key, settings.binance_api_secret)


def get_price_filter(symbol: str) -> dict:
    """Вернуть PRICE_FILTER (minPrice, maxPrice, tickSize и т.п.) для symbol."""
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
    raise ValueError(f"No PRICE_FILTER for symbol {symbol}")


def calculate_price(symbol: str, side: str) -> float:
    """
    Рассчитать maker-цену:
      BUY: best_bid - tickSize
      SELL: best_ask + tickSize
    Форматирует по правильной точности.
    """
    pf = get_price_filter(symbol)
    tick = float(pf["tickSize"])
    book = _client.futures_order_book(symbol=symbol, limit=5)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])

    raw = best_bid - tick if side.upper() == "BUY" else best_ask + tick
    # сколько знаков после запятой у tickSize
    decimals = abs(int(round(math.log10(tick))))
    return float(f"{raw:.{decimals}f}")


def cancel_open_orders(symbol: str, side: str = None):
    """
    Отменить все висящие LIMIT-ордера по символу.
    Если side указан, отменить только этого направления.
    """
    opens = _client.futures_get_open_orders(symbol=symbol)
    for o in opens:
        if o["type"] == "LIMIT" and (side is None or o["side"] == side):
            _client.futures_cancel_order(symbol=symbol, orderId=o["orderId"])


def wait_for_fill(symbol: str, order_id: int, timeout: float = 10.0, poll_interval: float = 0.5):
    """
    Опросить статус ордера до FILLED (или PARTIALLY_FILLED) в течение timeout секунд.
    Если не FILLED по истечении таймаута — бросить исключение.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        o = _client.futures_get_order(symbol=symbol, orderId=order_id)
        if o["status"] in ("FILLED", "PARTIALLY_FILLED"):
            return
        time.sleep(poll_interval)
    raise RuntimeError(f"Order {order_id} not filled within {timeout}s")


def place_post_only(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only (GTX) лимитный ордер на вход:
    1. Отменить старые висящие ордера того же side.
    2. Высчитать цену maker.
    3. Отправить ордер.
    4. Дождаться исполнения.
    """
    cancel_open_orders(symbol, side)
    price = calculate_price(symbol, side)
    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "price": str(price),
        "quantity": str(quantity),
    }
    order = _client.futures_create_order(**params)
    wait_for_fill(symbol, order["orderId"])
    return order


def place_post_only_exit(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only (GTX) ордер на закрытие позиции:
    1. Отменить старые висящие ордера противоположного side.
    2. Рассчитать цену выхода.
    3. Отправить ордер и дождаться fill.
    """
    # close_side — противоположный side
    close_side = "SELL" if side.upper() == "BUY" else "BUY"
    cancel_open_orders(symbol, close_side)
    price = calculate_price(symbol, close_side)
    params = {
        "symbol": symbol,
        "side": close_side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "price": str(price),
        "quantity": str(quantity),
    }
    order = _client.futures_create_order(**params)
    wait_for_fill(symbol, order["orderId"])
    return order


def get_position_amount(symbol: str) -> float:
    """
    Вернуть текущий объём позиции:
    >0 — long; <0 — short; 0 — без позиции.
    """
    positions = _client.futures_position_information()
    for p in positions:
        if p["symbol"] == symbol:
            return float(p.get("positionAmt", 0))
    return 0.0

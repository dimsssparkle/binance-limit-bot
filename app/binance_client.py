"""
app/binance_client.py

Обёртка над Binance Futures API:
- Методы для выставления Post-Only maker-ордеров на вход, выход и получение текущей позиции.
"""
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

# Инициализируем Binance-клиент один раз
_client = Client(settings.binance_api_key, settings.binance_api_secret)


def get_price_filter(symbol: str) -> dict:
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
    raise ValueError(f"Нет PRICE_FILTER для символа {symbol}")


def calculate_price(symbol: str, side: str) -> float:
    """
    Рассчитать цену с учётом maker:
      BUY: best_bid - tickSize
      SELL: best_ask + tickSize
    """
    pf = get_price_filter(symbol)
    tick = float(pf["tickSize"])
    book = _client.futures_order_book(symbol=symbol, limit=5)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])
    raw = best_bid - tick if side.upper() == "BUY" else best_ask + tick
    dec = abs(int(round(math.log10(tick))))
    return float(f"{raw:.{dec}f}")


def place_post_only(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only (GTX) maker-ордер по calculated price.
    """
    price = calculate_price(symbol, side)
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
    Закрыть позицию maker-ордером:
    закрывающий side — противоположный входному
    """
    close_side = "SELL" if side.upper() == "BUY" else "BUY"
    price = calculate_price(symbol, close_side)
    params = {
        "symbol": symbol,
        "side": close_side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "price": str(price),
        "quantity": str(quantity),
    }
    try:
        return _client.futures_create_order(**params)
    except BinanceAPIException:
        raise


def get_position_amount(symbol: str) -> float:
    """
    Текущий объем позиции: + для long, - для short.
    """
    positions = _client.futures_position_information()
    for p in positions:
        if p["symbol"] == symbol:
            return float(p.get("positionAmt", 0))
    return 0.0
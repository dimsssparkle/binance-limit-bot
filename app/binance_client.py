"""
app/binance_client.py

Обёртка над Binance Futures API:
- Расчёт maker-цен.
- Отмена висящих ордеров.
- Выставление Post-Only limiter-ордеров на вход и выход с ожиданием исполнения и логированием.
- Получение текущей позиции.
"""

import math
import time
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

# Настройка логера
logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

# Инициализируем клиента один раз
_client = Client(settings.binance_api_key, settings.binance_api_secret)


def get_price_filter(symbol: str) -> dict:
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
    raise ValueError(f"No PRICE_FILTER for symbol {symbol}")


def calculate_price(symbol: str, side: str) -> float:
    pf = get_price_filter(symbol)
    tick = float(pf["tickSize"])
    book = _client.futures_order_book(symbol=symbol, limit=5)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])
    raw = best_bid - tick if side.upper() == "BUY" else best_ask + tick
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
            logger.info(f"Cancel order {o['orderId']} side={o['side']}")
            _client.futures_cancel_order(symbol=symbol, orderId=o["orderId"])


def wait_for_fill(symbol: str, order_id: int, timeout: float = 20.0, poll_interval: float = 0.5):
    """
    Опрос статуса ордера до FILLED или PARTIALLY_FILLED.
    Логирует каждую попытку и бросает RuntimeError по таймауту.
    """
    deadline = time.time() + timeout
    attempt = 0
    logger.info(f"Waiting for fill of order {order_id} (timeout={timeout}s)")
    last_status = None
    while time.time() < deadline:
        attempt += 1
        o = _client.futures_get_order(symbol=symbol, orderId=order_id)
        status = o.get("status")
        logger.info(f"Order {order_id} status check #{attempt}: {status}")
        last_status = status
        if status in ("FILLED", "PARTIALLY_FILLED"):
            logger.info(f"Order {order_id} filled at attempt #{attempt}")
            return
        time.sleep(poll_interval)
    logger.error(f"Order {order_id} not filled within {timeout}s — last status: {last_status}")
    raise RuntimeError(f"Order {order_id} not filled within {timeout}s")


def place_post_only(symbol: str, side: str, quantity: float) -> dict:
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
    positions = _client.futures_position_information()
    for p in positions:
        if p["symbol"] == symbol:
            return float(p.get("positionAmt", 0))
    return 0.0
"""
app/binance_client.py

Обёртка над Binance Futures API:
- Расчёт maker-цен.
- Отмена висящих ордеров.
- Выставление Post-Only лимитных ордеров на вход и выход с ожиданием исполнения и логированием.
- Получение текущей позиции.
- Логирование истории стакана и позиций в локальные CSV.
"""

import os
import math
import time
import logging
from datetime import datetime
from pathlib import Path
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

# Настройка логера
logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

# Инициализируем Binance-клиент один раз
_client = Client(settings.binance_api_key, settings.binance_api_secret)

# Определяем корень проекта и папку data
BASE_DIR = Path(__file__).resolve().parent.parent  # /app
DATA_DIR = BASE_DIR / "data"
ORDER_BOOK_HISTORY = DATA_DIR / "order_book_history.csv"
POSITION_HISTORY = DATA_DIR / "position_history.csv"

# Создаём папку data и файлы с заголовками, если их нет
DATA_DIR.mkdir(exist_ok=True)
if not ORDER_BOOK_HISTORY.exists():
    ORDER_BOOK_HISTORY.write_text("timestamp,symbol,best_bid,best_ask\n")
if not POSITION_HISTORY.exists():
    POSITION_HISTORY.write_text("timestamp,symbol,positionAmt\n")


def log_order_book(symbol: str, best_bid: float, best_ask: float):
    """
    Добавить в ORDER_BOOK_HISTORY запись о текущем bid/ask.
    """
    ts = datetime.utcnow().isoformat()
    with ORDER_BOOK_HISTORY.open("a") as f:
        f.write(f"{ts},{symbol},{best_bid},{best_ask}\n")


def log_position(symbol: str, position_amt: float):
    """
    Добавить в POSITION_HISTORY запись о текущей позиции.
    """
    ts = datetime.utcnow().isoformat()
    with POSITION_HISTORY.open("a") as f:
        f.write(f"{ts},{symbol},{position_amt}\n")


def get_price_filter(symbol: str) -> dict:
    """
    Получить PRICE_FILTER (minPrice, tickSize и т.д.) из exchangeInfo.
    """
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
    И логирование best_bid/best_ask.
    """
    pf = get_price_filter(symbol)
    tick = float(pf["tickSize"])
    book = _client.futures_order_book(symbol=symbol, limit=5)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])

    # Логируем книгу ордеров
    log_order_book(symbol, best_bid, best_ask)

    raw = best_bid - tick if side.upper() == "BUY" else best_ask + tick
    decimals = abs(int(round(math.log10(tick))))
    return float(f"{raw:.{decimals}f}")


def cancel_open_orders(symbol: str, side: str = None):
    """
    Отменить все висящие LIMIT-ордера для symbol.
    Если указан side, ограничить отмену только этим направлением.
    """
    opens = _client.futures_get_open_orders(symbol=symbol)
    for o in opens:
        if o["type"] == "LIMIT" and (side is None or o["side"] == side):
            logger.info(f"Cancelling order {o['orderId']} side={o['side']}")
            _client.futures_cancel_order(symbol=symbol, orderId=o["orderId"])


def wait_for_fill(symbol: str, order_id: int, timeout: float = 20.0, poll_interval: float = 0.5):
    """
    Опросить статус ордера до FILLED или PARTIALLY_FILLED.
    Логировать каждую попытку и выбросить RuntimeError по таймауту.
    """
    deadline = time.time() + timeout
    attempt = 0
    last_status = None
    logger.info(f"Waiting for fill of order {order_id} (timeout={timeout}s)")
    while time.time() < deadline:
        attempt += 1
        o = _client.futures_get_order(symbol=symbol, orderId=order_id)
        status = o.get("status")
        logger.info(f"Order {order_id} status #{attempt}: {status}")
        last_status = status
        if status in ("FILLED", "PARTIALLY_FILLED"):
            logger.info(f"Order {order_id} filled at attempt {attempt}")
            return
        time.sleep(poll_interval)
    logger.error(f"Order {order_id} not filled within {timeout}s, last status {last_status}")
    raise RuntimeError(f"Order {order_id} not filled within {timeout}s")


def place_post_only(symbol: str, side: str, quantity: float) -> dict:
    """
    Выставить Post-Only (GTX) LIMIT-ордер на вход позиции:
    1) отменить предыдущие
    2) вычислить цену
    3) создать и дождаться fill
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
    Выставить Post-Only (GTX) LIMIT-ордер на закрытие позиции:
    1) отменить предыдущие
    2) вычислить цену выхода
    3) создать и дождаться fill
    """
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
    Вернуть текущий объём позиции: + для long, - для short. Логировать.
    """
    positions = _client.futures_position_information()
    for p in positions:
        if p["symbol"] == symbol:
            amt = float(p.get("positionAmt", 0))
            log_position(symbol, amt)
            return amt
    log_position(symbol, 0.0)
    return 0.0
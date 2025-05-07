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

# Файлы для истории
ORDER_BOOK_HISTORY = "/app/data/order_book_history.csv"
POSITION_HISTORY = "/app/data/position_history.csv"

# Инициализация файлов (создадим заголовки при старте)
import os
if not os.path.exists(os.path.dirname(ORDER_BOOK_HISTORY)):
    os.makedirs(os.path.dirname(ORDER_BOOK_HISTORY), exist_ok=True)
if not os.path.exists(ORDER_BOOK_HISTORY):
    with open(ORDER_BOOK_HISTORY, "w") as f:
        f.write("timestamp,symbol,best_bid,best_ask")
if not os.path.exists(POSITION_HISTORY):
    with open(POSITION_HISTORY, "w") as f:
        f.write("timestamp,symbol,positionAmt")


def log_order_book(symbol: str, best_bid: float, best_ask: float):
    """
    Логируем лучший бид/аск в CSV-файл.
    """
    from datetime import datetime
    ts = datetime.utcnow().isoformat()
    with open(ORDER_BOOK_HISTORY, "a") as f:
        f.write(f"{ts},{symbol},{best_bid},{best_ask}")


def log_position(symbol: str, position_amt: float):
    """
    Логируем текущее значение позиции в CSV-файл.
    """
    from datetime import datetime
    ts = datetime.utcnow().isoformat()
    with open(POSITION_HISTORY, "a") as f:
        f.write(f"{ts},{symbol},{position_amt}")


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

    # Логируем книгу ордеров
    log_order_book(symbol, best_bid, best_ask)

    raw = best_bid - tick if side.upper() == "BUY" else best_ask + tick
    decimals = abs(int(round(math.log10(tick))))
    return float(f"{raw:.{decimals}f}")


def get_position_amount(symbol: str) -> float:
    positions = _client.futures_position_information()
    for p in positions:
        if p["symbol"] == symbol:
            amt = float(p.get("positionAmt", 0))
            # Логируем позицию
            log_position(symbol, amt)
            return amt
    # Логируем нулевую позицию
    log_position(symbol, 0.0)
    return 0.0
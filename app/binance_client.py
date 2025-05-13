# File: app/binance_client.py
"""
app/binance_client.py

Обёртка над Binance Futures API:
- Интеграция WebSocket для market data.
- Динамический ретрай лимитных Post-Only ордеров с вычислением базовой цены из стакана.
- Логика истории стакана и позиций.
"""
import math
import time
import logging
from datetime import datetime
from pathlib import Path
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings
from app.websocket_manager import latest_book

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

# Инициализация Binance-клиента
_client = Client(settings.binance_api_key, settings.binance_api_secret)

# Пути к CSV
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ORDER_BOOK_HISTORY = DATA_DIR / "order_book_history.csv"
POSITION_HISTORY   = DATA_DIR / "position_history.csv"


def init_data() -> None:
    """
    Инициализирует директорию и файлы для логов стакана и позиций.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ORDER_BOOK_HISTORY.exists():
        ORDER_BOOK_HISTORY.write_text("timestamp,symbol,best_bid,best_ask")
        logger.info(f"Created {ORDER_BOOK_HISTORY}")
    if not POSITION_HISTORY.exists():
        POSITION_HISTORY.write_text("timestamp,symbol,positionAmt")
        logger.info(f"Created {POSITION_HISTORY}")


def log_order_book(symbol: str, best_bid: float, best_ask: float) -> None:
    """Логирует текущие лучшие bid/ask в CSV."""
    ts = datetime.utcnow().isoformat()
    with ORDER_BOOK_HISTORY.open("a") as f:
        f.write(f"{ts},{symbol},{best_bid},{best_ask}")


def log_position(symbol: str, position_amt: float) -> None:
    """Логирует текущий размер позиции в CSV."""
    ts = datetime.utcnow().isoformat()
    with POSITION_HISTORY.open("a") as f:
        f.write(f"{ts},{symbol},{position_amt}")


def get_price_filter(symbol: str) -> dict:
    """Возвращает PRICE_FILTER для символа."""
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
    raise ValueError(f"No PRICE_FILTER for symbol {symbol}")


def get_current_book(symbol: str) -> dict:
    """Возвращает лучшие bid/ask из WebSocket или REST."""
    book = latest_book.get(symbol)
    if book:
        return book
    resp = _client.futures_order_book(symbol=symbol, limit=5)
    return {"bid": float(resp["bids"][0][0]), "ask": float(resp["asks"][0][0])}


def wait_for_fill(symbol: str, order_id: int, timeout: float = 20.0, poll_interval: float = 0.5) -> None:
    """
    Ожидает заполнения (FILLED/PARTIALLY_FILLED) лимитного ордера.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        o = _client.futures_get_order(symbol=symbol, orderId=order_id)
        status = o.get("status")
        logger.info(f"Order {order_id} status: {status}")
        if status in ("FILLED", "PARTIALLY_FILLED"):
            return
        time.sleep(poll_interval)
    raise RuntimeError(f"Order {order_id} not filled in {timeout}s, last status {status}")


def place_post_only_with_retries(
    symbol: str,
    side: str,
    quantity: float,
    max_deviation_pct: float = 0.1,
    retry_interval: float = 0.5,  # уменьшенный интервал для более частых попыток
    max_attempts: int = 20
) -> dict:
    """
    Выставляет post-only лимитный ордер и продолжает попытки размещения при любых отказах,
    пока не заполнится, либо не исчерпает max_attempts, либо не превысит допустимое отклонение:
      - Для BUY: price_raw = best_bid - tick * attempt;
      - Для SELL: price_raw = best_ask + tick * attempt;
    После каждой неудачной попытки (API exception с reject или статус NEW без fill) повторяет.
    """
    pf = get_price_filter(symbol)
    tick = float(pf["tickSize"])
    last_order_id = None

    for attempt in range(1, max_attempts + 1):
        book = get_current_book(symbol)
        best_bid, best_ask = book["bid"], book["ask"]
        # Расчет цены с учетом итерации
        if side.upper() == "BUY":
            price_raw = best_bid - tick * attempt
            base_price = best_bid
        else:
            price_raw = best_ask + tick * attempt
            base_price = best_ask
        # Проверяем отклонение
        max_dev = base_price * max_deviation_pct / 100
        if abs(price_raw - base_price) > max_dev:
            logger.error(f"Exceeded max deviation after {attempt} attempts")
            break
        precision = abs(int(round(math.log10(tick))))
        price_str = f"{price_raw:.{precision}f}"

        # Отменяем предыдущий ордер, если есть
        if last_order_id:
            try:
                _client.futures_cancel_order(symbol=symbol, orderId=last_order_id)
                logger.info(f"Cancelled previous order {last_order_id}")
            except Exception:
                pass
            last_order_id = None

        # Пытаемся создать ордер
        try:
            order = _client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type="LIMIT",
                timeInForce="GTX",
                price=price_str,
                quantity=str(quantity)
            )
            last_order_id = order["orderId"]
            logger.info(f"Attempt {attempt}/{max_attempts}: placed post-only {side} at {price_str}")
        except BinanceAPIException as e:
            # При отказе как maker продолжаем попытки
            if "could not be executed as maker" in e.message or "Post Only order will be rejected" in e.message:
                logger.warning(f"Attempt {attempt}: maker reject ({e.message}), retrying")
                time.sleep(retry_interval)
                continue
            # Любые другие ошибки прерывают цикл
            logger.error(f"Attempt {attempt}: unexpected API error ({e.message})")
            raise

        # Ждем перед проверкой статуса
        time.sleep(retry_interval)
        try:
            o = _client.futures_get_order(symbol=symbol, orderId=last_order_id)
            status = o.get("status")
        except BinanceAPIException as e:
            logger.warning(f"Attempt {attempt}: cannot fetch order status, retrying")
            time.sleep(retry_interval)
            continue

        logger.info(f"Order {last_order_id} status: {status}")
        if status in ("FILLED", "PARTIALLY_FILLED"):
            return o
        # Иначе — повторяем попытку
        time.sleep(retry_interval)

    error = f"Order {side} {symbol} {quantity} not filled after {max_attempts} attempts"
    logger.error(error)
    raise RuntimeError(error)

def get_position_amount(symbol: str) -> float:
    """Возвращает текущий размер позиции и логирует его."""
    positions = _client.futures_position_information()
    for p in positions:
        if p['symbol'] == symbol:
            amt = float(p.get('positionAmt', 0))
            log_position(symbol, amt)
            return amt
    log_position(symbol, 0.0)
    return 0.0

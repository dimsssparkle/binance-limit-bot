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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ORDER_BOOK_HISTORY.exists():
        ORDER_BOOK_HISTORY.write_text("timestamp,symbol,best_bid,best_ask\n")
        logger.info(f"Created {ORDER_BOOK_HISTORY}")
    if not POSITION_HISTORY.exists():
        POSITION_HISTORY.write_text("timestamp,symbol,positionAmt\n")
        logger.info(f"Created {POSITION_HISTORY}")


def log_order_book(symbol: str, best_bid: float, best_ask: float) -> None:
    ts = datetime.utcnow().isoformat()
    with ORDER_BOOK_HISTORY.open("a") as f:
        f.write(f"{ts},{symbol},{best_bid},{best_ask}\n")


def log_position(symbol: str, position_amt: float) -> None:
    ts = datetime.utcnow().isoformat()
    with POSITION_HISTORY.open("a") as f:
        f.write(f"{ts},{symbol},{position_amt}\n")


def get_price_filter(symbol: str) -> dict:
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
    raise ValueError(f"No PRICE_FILTER for symbol {symbol}")


def cancel_open_orders(symbol: str, side: str = None) -> None:
    opens = _client.futures_get_open_orders(symbol=symbol)
    for o in opens:
        if o["type"] == "LIMIT" and (side is None or o["side"] == side):
            logger.info(f"Cancelling order {o['orderId']} side={o['side']}")
            _client.futures_cancel_order(symbol=symbol, orderId=o["orderId"])


def wait_for_fill(symbol: str, order_id: int, timeout: float = 20.0, poll_interval: float = 0.5) -> None:
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


def get_current_book(symbol: str) -> dict[str, float]:
    """
    Возвращает {'bid': float, 'ask': float}.
    Сначала пытается из WebSocket latest_book; если нет — REST.
    """
    book = latest_book.get(symbol)
    if book:
        return book
    # REST-fallback
    resp = _client.futures_order_book(symbol=symbol, limit=5)
    bid = float(resp["bids"][0][0])
    ask = float(resp["asks"][0][0])
    return {"bid": bid, "ask": ask}


def place_post_only_with_retries(
    symbol: str,
    side: str,
    quantity: float,
    max_deviation_pct: float = 0.1,
    retry_interval: float = 1.0
) -> dict:
    """
    Динамический retry без внешнего base_price.
    Базовая цена = best_bid (для BUY) или best_ask (для SELL) из WebSocket/REST.
    """
    pf = get_price_filter(symbol)
    tick = float(pf["tickSize"])

    # Получаем базовую цену
    book = get_current_book(symbol)
    base_price = book["bid"] if side.upper() == "BUY" else book["ask"]

    # Границы отклонения
    max_dev_abs = base_price * max_deviation_pct / 100
    max_offset = max_dev_abs + tick
    offset = 0.0
    last_order_id = None

    while True:
        price_raw = (base_price + offset) if side.upper() == "BUY" else (base_price - offset)
        price = float(f"{price_raw:.{abs(int(round(math.log10(tick))))}f}")

        # Отменяем предыдущий ордер, если был
        if last_order_id:
            try:
                _client.futures_cancel_order(symbol=symbol, orderId=last_order_id)
                logger.info(f"Cancelled previous order {last_order_id}")
            except Exception:
                pass

        # Пытаемся создать новый ордер
        try:
            order = _client.futures_create_order(
                symbol=symbol,
                side=side,
                type="LIMIT",
                timeInForce="GTX",
                price=str(price),
                quantity=str(quantity)
            )
        except BinanceAPIException as e:
            # Если заявка отклонена как taker, продолжаем ретрай с увеличенным offset
            if "Post Only order will be rejected" in e.message:
                logger.info(f"Post-Only rejected at price {price}: retrying")
                offset += tick
                if offset > max_offset:
                    raise RuntimeError(f"Exceeded max deviation {max_dev_abs:.2f}, aborting retries")
                continue
            # В остальных случаях пробрасываем ошибку
            raise

        last_order_id = order["orderId"]
        logger.info(f"Placed order {last_order_id} at price {price}, offset={offset}")

        # Ждем небольшой промежуток перед проверкой
        time.sleep(retry_interval)

        # Проверяем статус
        o = _client.futures_get_order(symbol=symbol, orderId=last_order_id)
        status = o.get("status")
        logger.info(f"Order {last_order_id} status: {status}")
        if status in ("FILLED", "PARTIALLY_FILLED"):
            return order

        # Увеличиваем смещение и повторяем
        offset += tick
        if offset > max_offset:
            _client.futures_cancel_order(symbol=symbol, orderId=last_order_id)
            raise RuntimeError(f"Exceeded max deviation {max_dev_abs:.2f}, aborting retries")




def get_position_amount(symbol: str) -> float:
    """
    Возвращает текущий размер позиции для symbol и логгирует его.
    """
    positions = _client.futures_position_information()
    for p in positions:
        if p['symbol'] == symbol:
            amt = float(p.get('positionAmt', 0))
            log_position(symbol, amt)
            return amt
    log_position(symbol, 0.0)
    return 0.0
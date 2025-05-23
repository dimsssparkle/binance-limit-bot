import math
import time
import logging
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings
from app.websocket_manager import latest_book

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

_client = Client(settings.binance_api_key, settings.binance_api_secret)


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


def get_current_book(symbol: str) -> dict:
    book = latest_book.get(symbol)
    if book:
        return book
    resp = _client.futures_order_book(symbol=symbol, limit=5)
    return {"bid": float(resp["bids"][0][0]), "ask": float(resp["asks"][0][0])}


def wait_for_fill(symbol: str, order_id: int, timeout: float = 20.0, poll_interval: float = 0.5) -> None:
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
    retry_interval: float = 1,
    max_attempts: int = 10
) -> dict:
    pf = get_price_filter(symbol)
    tick = float(pf["tickSize"])
    initial_pos = get_position_amount(symbol)
    target_pos = initial_pos + quantity if side.upper() == 'BUY' else initial_pos - quantity
    last_order_id = None

    for attempt in range(1, max_attempts + 1):
        current_pos = get_position_amount(symbol)
        if (side.upper() == 'BUY' and current_pos >= target_pos) or \
           (side.upper() == 'SELL' and current_pos <= target_pos):
            logger.info(f"Position reached target ({current_pos}), stopping retries")
            return {'orderId': last_order_id}

        book = get_current_book(symbol)
        best_bid, best_ask = book['bid'], book['ask']
        if side.upper() == 'BUY':
            price_raw = best_bid - tick * attempt
            base_price = best_bid
        else:
            price_raw = best_ask + tick * attempt
            base_price = best_ask
        max_dev = base_price * max_deviation_pct / 100
        if abs(price_raw - base_price) > max_dev:
            logger.error(f"Exceeded max deviation after {attempt} attempts")
            break
        precision = abs(int(round(math.log10(tick))))
        price_str = f"{price_raw:.{precision}f}"

        if last_order_id:
            try:
                _client.futures_cancel_order(symbol=symbol, orderId=last_order_id)
            except Exception:
                pass
            last_order_id = None

        try:
            order = _client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type="LIMIT",
                timeInForce="GTX",
                price=price_str,
                quantity=str(quantity)
            )
            last_order_id = order['orderId']
            logger.info(f"Attempt {attempt}/{max_attempts}: placed post-only {side} at {price_str}")
        except BinanceAPIException as e:
            if "could not be executed as maker" in e.message or \
               "Post Only order will be rejected" in e.message:
                logger.warning(f"Attempt {attempt}: maker reject ({e.message}), retrying")
                time.sleep(retry_interval)
                continue
            logger.error(f"Attempt {attempt}: unexpected API error ({e.message})")
            raise

        time.sleep(retry_interval)
        try:
            o = _client.futures_get_order(symbol=symbol, orderId=last_order_id)
            status = o.get('status')
        except BinanceAPIException:
            logger.warning(f"Attempt {attempt}: cannot fetch order status, retrying")
            time.sleep(retry_interval)
            continue

        logger.info(f"Order {last_order_id} status: {status}")
        if status in ('FILLED', 'PARTIALLY_FILLED'):
            return o
        time.sleep(retry_interval)

    error = f"Order {side} {symbol} {quantity} not filled after {max_attempts} attempts"
    logger.error(error)
    raise RuntimeError(error)


def get_position_amount(symbol: str) -> float:
    positions = _client.futures_position_information()
    for p in positions:
        if p['symbol'] == symbol:
            amt = float(p.get('positionAmt', 0))
            logger.debug(f"Position for {symbol}: {amt}")
            return amt
    logger.debug(f"No position for {symbol}, returning 0.0")
    return 0.0
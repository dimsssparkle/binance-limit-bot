import logging
from threading import Thread
from binance import ThreadedWebsocketManager
from app.config import settings

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

_order_books: dict[str, dict] = {}

latest_book = _order_books

_twm = ThreadedWebsocketManager(
    api_key=settings.binance_api_key,
    api_secret=settings.binance_api_secret
)

def _on_depth_update(msg):
    payload = msg.get('data', msg)
    symbol = payload.get('s')
    if not symbol:
        return
    bids = payload.get('b', [])
    asks = payload.get('a', [])
    _order_books[symbol] = {
        'symbol': symbol,
        'bids': bids,
        'asks': asks,
        'timestamp': payload.get('E')
    }
    logger.debug(f"Depth update {symbol}: bids={len(bids)}, asks={len(asks)}")

def get_order_book_snapshot(symbol: str = 'ETHUSDT') -> dict:
    default = {'symbol': symbol, 'bids': [], 'asks': [], 'timestamp': None}
    return _order_books.get(symbol, default)

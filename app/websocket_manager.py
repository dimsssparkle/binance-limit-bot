"""
app/websocket_manager.py

WebSocket-менеджер для Binance Futures depth stream.
Хранит в памяти latest_book с лучшими bid/ask для каждой symbol.
"""
from threading import Thread
from binance import ThreadedWebsocketManager
from app.config import settings
import logging

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

latest_book: dict[str, dict[str, float]] = {}

_twm = ThreadedWebsocketManager(
    api_key=settings.binance_api_key,
    api_secret=settings.binance_api_secret
)

def _on_depth_update(msg):
    symbol = msg['s']
    bids = msg.get('b', [])
    asks = msg.get('a', [])
    if bids and asks:
        latest_book[symbol] = {
            'bid': float(bids[0][0]),
            'ask': float(asks[0][0])
        }
        logger.debug(f"Depth update {symbol}: bid={latest_book[symbol]['bid']}, ask={latest_book[symbol]['ask']}")

# app/websocket_manager.py
latest_depth = {}

def start_websocket(symbols: list[str]):
    def on_depth_update(symbol, asks, bids):
        latest_depth[symbol] = {"asks": asks, "bids": bids}
    # ваша логика подписки на Binance и вызова on_depth_update

    def runner():
        _twm.start()
        for s in symbols:
            logger.info(f"Subscribing to futures depth socket for {s}")
            _twm.start_futures_depth_socket(callback=_on_depth_update, symbol=s)
        _twm.join()

    thread = Thread(target=runner, daemon=True)
    thread.start()

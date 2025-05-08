"""
app/websocket_manager.py

WebSocket-менеджер для получения глубины стакана в реальном времени.
Хранит в памяти latest_book с лучшими bid/ask для каждой symbol.
"""
from threading import Thread
from binance import ThreadedWebsocketManager
from app.config import settings
import logging

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

# В памяти: symbol -> {'bid': float, 'ask': float}
latest_book: dict[str, dict[str, float]] = {}

# Инициализируем WebSocket Manager
_twm = ThreadedWebsocketManager(
    api_key=settings.binance_api_key,
    api_secret=settings.binance_api_secret
)


def _on_depth_update(msg):
    symbol = msg['s']  # e.g. 'ETHUSDT'
    bids = msg.get('b', [])
    asks = msg.get('a', [])
    if bids and asks:
        latest_book[symbol] = {
            'bid': float(bids[0][0]),
            'ask': float(asks[0][0])
        }


def start_websocket(symbols: list[str]) -> None:
    """
    Запускает WebSocket-поток и подписывается на depth для каждого symbol.
    """
    def runner():
        _twm.start()
        for s in symbols:
            logger.info(f"Subscribing to depth socket for {s}")
            _twm.start_depth_socket(callback=_on_depth_update, symbol=s)
        _twm.join()

    thread = Thread(target=runner, daemon=True)
    thread.start()
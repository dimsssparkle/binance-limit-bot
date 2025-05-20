"""
app/websocket_manager.py

WebSocket-менеджер для Binance Futures depth stream.
Хранит в памяти последний снимок стакана (bids/asks) для каждого symbol.
"""
import logging
from threading import Thread
from binance import ThreadedWebsocketManager
from app.config import settings

logger = logging.getLogger(__name__)
logger.setLevel(settings.log_level)

# Кеш последних стаканов: symbol -> {'bids': [...], 'asks': [...], 'timestamp': ...}
_order_books: dict[str, dict] = {}

# Инициализация менеджера с ключами API
_twm = ThreadedWebsocketManager(
    api_key=settings.binance_api_key,
    api_secret=settings.binance_api_secret
)


def _on_depth_update(msg):
    """
    Callback для каждого сообщения из Binance WS.
    msg может быть обёрткой combined stream: {'stream':..., 'data':{...}}
    Поэтому сначала распаковываем.
    """
    payload = msg.get('data', msg)

    symbol = payload.get('s')
    if not symbol:
        # нет символа — игнорируем
        return

    bids = payload.get('b', [])
    asks = payload.get('a', [])

    # Сохраняем полный список глубины
    _order_books[symbol] = {
        'symbol': symbol,
        'bids': bids,
        'asks': asks,
        'timestamp': payload.get('E')
    }
    logger.debug(f"Depth update {symbol}: bids={len(bids)}, asks={len(asks)}")


def start_websocket(symbols: list[str]):
    """
    Запускает поток WebSocket для передачи depth20@100ms по списку символов.
    """
    def runner():
        _twm.start()
        for s in symbols:
            logger.info(f"Subscribing to futures depth socket for {s}")
            # подписка на 20 уровней глубины с интервалом 100ms
            _twm.start_depth_socket(
                callback=_on_depth_update,
                symbol=s,
                depth=20,
                interval='100ms'
            )
        # блокируем поток, читая данные WebSocket
        _twm.join()

    thread = Thread(target=runner, daemon=True)
    thread.start()


def get_order_book_snapshot(symbol: str = 'ETHUSDT') -> dict:
    """
    Возвращает последний известный стакан для symbol,
    или пустой шаблон, если ещё нет данных.
    """
    default = {'symbol': symbol, 'bids': [], 'asks': [], 'timestamp': None}
    return _order_books.get(symbol, default)

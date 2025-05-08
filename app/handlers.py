"""
app/handlers.py

Обработка сигналов без передачи цены:
- `price` больше не поле модели.
- Базовая цена берётся из WebSocket.
"""
from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import init_data, place_post_only_with_retries

# Инициализируем папку и файлы
init_data()

class Signal(BaseModel):
    symbol: str
    side: str
    quantity: float
    action: str = 'open'

    @validator('side')
    def validate_side(cls, v):
        v2 = v.upper()
        if v2 not in ('BUY', 'SELL'):
            raise ValueError("Поле 'side' должно быть 'BUY' или 'SELL'")
        return v2

    @validator('action')
    def validate_action(cls, v):
        v2 = v.lower()
        if v2 not in ('open', 'close'):
            raise ValueError("Поле 'action' должно быть 'open' или 'close'")
        return v2

def handle_signal(data: dict) -> dict:
    try:
        sig = Signal(**data)

        if sig.action == 'close':
            return {'status': 'error', 'detail': 'Close not implemented'}

        # Для открытия позиции применяем retry по стакану
        order = place_post_only_with_retries(
            symbol=sig.symbol,
            side=sig.side,
            quantity=sig.quantity,
            max_deviation_pct=0.1,
            retry_interval=1.0
        )
        return {'status': 'ok', 'detail': f"order_id={order['orderId']}"}

    except BinanceAPIException as e:
        return {'status': 'error', 'detail': f"BinanceAPI: {e.message}"}
    except RuntimeError as e:
        return {'status': 'error', 'detail': str(e)}
    except ValueError as e:
        return {'status': 'error', 'detail': str(e)}

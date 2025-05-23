from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    get_position_amount,
    place_post_only_with_retries
)
from threading import Lock
from collections import defaultdict

symbol_locks: dict[str, Lock] = defaultdict(Lock)

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
    sig = Signal(**data)
    lock = symbol_locks[sig.symbol]
    if not lock.acquire(blocking=False):
        return {'status': 'error', 'detail': 'Operation already in progress for symbol'}
    try:
        if sig.action == 'close':
            current_amt = get_position_amount(sig.symbol)
            if current_amt == 0:
                return {'status': 'error', 'detail': 'Нет позиции для закрытия'}
            close_side = 'SELL' if current_amt > 0 else 'BUY'
            qty = abs(current_amt)
            order = place_post_only_with_retries(
                symbol=sig.symbol,
                side=close_side,
                quantity=qty
            )
            return {'status': 'ok', 'detail': f"closed_order_id={order['orderId']}"}

        order = place_post_only_with_retries(
            symbol=sig.symbol,
            side=sig.side,
            quantity=sig.quantity
        )
        return {'status': 'ok', 'detail': f"order_id={order['orderId']}"}

    except BinanceAPIException as e:
        return {'status': 'error', 'detail': f"BinanceAPI: {e.message}"}
    except RuntimeError as e:
        return {'status': 'error', 'detail': str(e)}
    except ValueError as e:
        return {'status': 'error', 'detail': str(e)}
    finally:
        lock.release()
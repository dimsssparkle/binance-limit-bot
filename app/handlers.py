# app/handlers.py

"""
app/handlers.py

Бизнес-логика обработки сигналов:
- Поддержка action='open' (с авто-флипом) и action='close'.
- Выставление maker-ордеров (Post-Only) на вход/выход.
- Обработка ошибок BinanceAPI, ValueError и RuntimeError.
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    get_position_amount,
    place_post_only,
    place_post_only_exit
)

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
    """
    Обработка сигнала:
      - 'close': закрытие текущей позиции maker-ордером.
      - 'open': авто-флип (закрытие противоположной, затем открытие новой).
    Возвращает JSON-словарь с status и detail.
    """
    try:
        sig = Signal(**data)
        current_amt = get_position_amount(sig.symbol)

        if sig.action == 'close':
            if current_amt == 0:
                return {'status': 'error', 'detail': 'Нет позиции для закрытия'}
            order = place_post_only_exit(sig.symbol, sig.side, sig.quantity)
            return {'status': 'ok', 'detail': f"closed_order_id={order['orderId']}"}

        # action == 'open'
        if current_amt < 0 and sig.side == 'BUY':
            place_post_only_exit(sig.symbol, 'SELL', abs(current_amt))
        elif current_amt > 0 and sig.side == 'SELL':
            place_post_only_exit(sig.symbol, 'BUY', current_amt)

        order = place_post_only(sig.symbol, sig.side, sig.quantity)
        return {'status': 'ok', 'detail': f"order_id={order['orderId']}"}

    except BinanceAPIException as e:
        return {'status': 'error', 'detail': f"BinanceAPI: {e.message}"}
    except RuntimeError as e:
        return {'status': 'error', 'detail': str(e)}
    except ValueError as e:
        return {'status': 'error', 'detail': str(e)}
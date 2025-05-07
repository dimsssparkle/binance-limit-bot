# app/handlers.py

"""
app/handlers.py

Бизнес-логика входа/выхода с Post-Only maker-ордерами, авто-флипом и явным action.
Теперь импортируем place_post_only и place_post_only_exit из binance_client.
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
            # closing short
            place_post_only_exit(sig.symbol, 'SELL', abs(current_amt))
        elif current_amt > 0 and sig.side == 'SELL':
            # closing long
            place_post_only_exit(sig.symbol, 'BUY', current_amt)

        order = place_post_only(sig.symbol, sig.side, sig.quantity)
        return {'status': 'ok', 'detail': f"order_id={order['orderId']}"}

    except BinanceAPIException as e:
        return {'status': 'error', 'detail': f"BinanceAPI: {e.message}"}
    except RuntimeError as e:
        return {'status': 'error', 'detail': str(e)}
    except ValueError as e:
        return {'status': 'error', 'detail': str(e)}

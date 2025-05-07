# app/handlers.py

"""
app/handlers.py

Бизнес-логика входа/выхода с Post-Only maker-ордерами и авто-флипом.
Теперь ловим RuntimeError от таймаута ожидания исполнения и возвращаем JSON.
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    get_position_amount,
    place_post_only,
    place_post_only_exit
)


class Signal(BaseModel):
    symbol: str               # торговая пара, например 'ETHUSDT'
    side: str                 # 'BUY' или 'SELL'
    quantity: float           # объём позиции
    action: str = 'open'      # 'open' или 'close'

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
    Логика обработки сигнала:
    - action='close': закрываем текущую позицию maker-ордером.
    - action='open': auto-flip (закрытие противоположной позиции + открытие новой).
    Ошибки BinanceAPI, ValueError и RuntimeError (таймаут) превращаем в JSON.
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
        # 1) Если есть противоположная позиция — закрываем её
        if current_amt < 0 and sig.side == 'BUY':
            place_post_only_exit(sig.symbol, 'SELL', abs(current_amt))
        elif current_amt > 0 and sig.side == 'SELL':
            place_post_only_exit(sig.symbol, 'BUY', current_amt)

        # 2) Открываем новую позицию
        order = place_post_only(sig.symbol, sig.side, sig.quantity)
        return {'status': 'ok', 'detail': f"order_id={order['orderId']}"}

    except BinanceAPIException as e:
        return {'status': 'error', 'detail': f"BinanceAPI: {e.message}"}

    except RuntimeError as e:
        # Таймаут ожидания исполнения в wait_for_fill
        return {'status': 'error', 'detail': str(e)}

    except ValueError as e:
        return {'status': 'error', 'detail': str(e)}

"""
app/handlers.py

Исправленная логика «флипа» позиции:
1. При action='open' и наличии противоположной позиции:
   a) Сначала выставляем и дожидаемся fill close-ордера.
   b) Только после него — выставляем новый open-ордер.
2. Всё по-прежнему в режиме Post-Only с maker-комиссией.
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    get_position_amount,
    place_post_only,
    place_post_only_exit
)


class Signal(BaseModel):
    symbol: str               # например 'ETHUSDT'
    side: str                 # 'BUY' или 'SELL'
    quantity: float           # объём
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
    try:
        sig = Signal(**data)
        current_amt = get_position_amount(sig.symbol)

        # 1) Закрытие текущей позиции, если нужно
        if sig.action == 'close':
            if current_amt == 0:
                return {'status': 'error', 'detail': 'Нет позиции для закрытия'}
            close_order = place_post_only_exit(sig.symbol, sig.side, sig.quantity)
            return {'status': 'ok', 'detail': f"closed_order_id={close_order['orderId']}"}

        # 2) Флип (open) — если есть противоположная позиция, закрываем её первой
        open_order = None
        if current_amt < 0 and sig.side == 'BUY':
            # у нас short, а пришёл BUY — сначала закрытие short
            place_post_only_exit(sig.symbol, 'SELL', abs(current_amt))
            # теперь открываем long
            open_order = place_post_only(sig.symbol, sig.side, sig.quantity)

        elif current_amt > 0 and sig.side == 'SELL':
            # у нас long, а пришёл SELL — сначала закрытие long
            place_post_only_exit(sig.symbol, 'BUY', current_amt)
            # теперь открываем short
            open_order = place_post_only(sig.symbol, sig.side, sig.quantity)

        else:
            # либо позиции нет, либо сигнал совпадает с текущим направлением
            open_order = place_post_only(sig.symbol, sig.side, sig.quantity)

        return {'status': 'ok', 'detail': f"order_id={open_order['orderId']}"}

    except BinanceAPIException as e:
        return {'status': 'error', 'detail': f"BinanceAPI: {e.message}"}
    except ValueError as e:
        return {'status': 'error', 'detail': str(e)}

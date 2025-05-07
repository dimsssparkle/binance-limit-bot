"""
app/handlers.py

Бизнес-логика обработки сигналов с явным action:
- action='open' (по умолчанию): авто-флип или открытие новой
- action='close': только закрытие текущей позиции maker-ордером
"""
from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    get_position_amount,
    place_post_only,
    place_post_only_exit
)

class Signal(BaseModel):
    symbol: str               # торговая пара
    side: str                 # 'BUY' или 'SELL'
    quantity: float           # объем позиции
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
        amt = get_position_amount(sig.symbol)

        if sig.action == 'close':
            if amt == 0:
                return {'status':'error', 'detail':'Нет позиции для закрытия'}
            order = place_post_only_exit(sig.symbol, sig.side, sig.quantity)
        else:
            # auto-flip: если позиция противоположна — закрываем её
            if amt < 0 and sig.side == 'BUY':
                place_post_only_exit(sig.symbol, 'SELL', abs(amt))
            elif amt > 0 and sig.side == 'SELL':
                place_post_only_exit(sig.symbol, 'BUY', amt)
            # затем открываем новую
            order = place_post_only(sig.symbol, sig.side, sig.quantity)

        return {'status':'ok', 'detail':f"order_id={order.get('orderId')}"}

    except BinanceAPIException as e:
        return {'status':'error', 'detail':f"BinanceAPI: {e.message}"}
    except ValueError as e:
        return {'status':'error', 'detail':str(e)}
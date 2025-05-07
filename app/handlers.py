# app/handlers.py

"""
app/handlers.py

Бизнес-логика входа/выхода с авто-флипом:
- action = 'open' (по умолчанию): 
    • если есть противоположная позиция — закрыть её maker-ордером
    • затем открыть новую maker-ордером
- action = 'close': закрыть текущую позицию maker-ордером
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    get_position_amount,
    place_post_only,
    place_post_only_exit
)


class Signal(BaseModel):
    symbol: str               # торговая пара: e.g. 'ETHUSDT'
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
    Основная логика:
    1) Валидировать входной JSON.
    2) В зависимости от action:
       - 'close': просто закрыть текущую позицию maker-ордером.
       - 'open': auto-flip или открытие:
           a) если есть противоположная позиция — закрыть её
           b) затем открыть новую позицию
    3) Вернуть статус и order_id.
    """
    try:
        sig = Signal(**data)
        current = get_position_amount(sig.symbol)

        if sig.action == 'close':
            if current == 0:
                return {'status': 'error', 'detail': 'Нет позиции для закрытия'}
            order = place_post_only_exit(sig.symbol, sig.side, sig.quantity)

        else:  # open
            # 1) Если противоположная позиция — закроем её
            if current < 0 and sig.side == 'BUY':
                place_post_only_exit(sig.symbol, 'SELL', abs(current))
            elif current > 0 and sig.side == 'SELL':
                place_post_only_exit(sig.symbol, 'BUY', current)

            # 2) Открываем новую позицию
            order = place_post_only(sig.symbol, sig.side, sig.quantity)

        return {'status': 'ok', 'detail': f"order_id={order['orderId']}"}

    except BinanceAPIException as e:
        return {'status': 'error', 'detail': f"BinanceAPI: {e.message}"}
    except ValueError as e:
        return {'status': 'error', 'detail': str(e)}

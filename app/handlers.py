"""
app/handlers.py

Бизнес-логика обработки сигналов:
- Вход: выставление позиции по Post-Only + смещение.
- Выход: закрытие позиции по Post-Only + смещение.
- Валидация через Pydantic.
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    place_post_only_smart_order,
    place_post_only_smart_exit
)

class Signal(BaseModel):
    """
    Модель сигнала, пришедшего из TradingView:
    - symbol: валютная пара, например 'ETHUSDT'
    - side: 'BUY' или 'SELL'
    - quantity: объём позиции
    - action: 'open' или 'close' (опционально, default='open')
    """
    symbol: str
    side: str
    quantity: float
    action: str = "open"

    @validator("side")
    def side_must_be_valid(cls, v):
        v_up = v.upper()
        if v_up not in ("BUY", "SELL"):
            raise ValueError("Поле 'side' должно быть 'BUY' или 'SELL'")
        return v_up

    @validator("action")
    def action_must_be_valid(cls, v):
        v_low = v.lower()
        if v_low not in ("open", "close"):
            raise ValueError("Поле 'action' должно быть 'open' или 'close'")
        return v_low

def handle_signal(data: dict) -> dict:
    """
    Обрабатывает сигнал:
    1. Валидирует через Pydantic.
    2. По action='open' вызывает place_post_only_smart_order.
       По action='close' — place_post_only_smart_exit.
    3. Возвращает {'status':'ok','detail':'order_id=...'} или {'status':'error',...}.
    """
    try:
        sig = Signal(**data)

        if sig.action == "close":
            order = place_post_only_smart_exit(
                symbol=sig.symbol,
                side=sig.side,
                quantity=sig.quantity
            )
        else:
            order = place_post_only_smart_order(
                symbol=sig.symbol,
                side=sig.side,
                quantity=sig.quantity
            )

        return {"status": "ok", "detail": f"order_id={order.get('orderId')}"}

    except BinanceAPIException as e:
        return {"status": "error", "detail": f"BinanceAPI: {e.message}"}

    except ValueError as e:
        return {"status": "error", "detail": str(e)}

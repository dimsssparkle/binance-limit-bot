# app/handlers.py

from pydantic import BaseModel, validator
from app.binance_client import place_limit_order

class Signal(BaseModel):
    symbol: str
    side: str           # "BUY" или "SELL"
    price: float
    quantity: float

    @validator("side")
    def side_must_be_valid(cls, v):
        v_up = v.upper()
        if v_up not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        return v_up

def handle_signal(data: dict) -> str:
    """
    Принимает данные из webhook, валидирует их и
    выставляет лимитный ордер на Binance.
    """
    # 1) Валидируем вход
    signal = Signal(**data)

    # 2) Отправляем ордер
    order = place_limit_order(
        symbol=signal.symbol,
        side=signal.side,
        price=signal.price,
        quantity=signal.quantity
    )

    # 3) Возвращаем информацию об ордере
    return f"order_id={order.get('orderId')}"

# app/handlers.py

from pydantic import BaseModel, validator
from app.binance_client import place_limit_order
from binance.exceptions import BinanceAPIException

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

def handle_signal(data: dict) -> dict:
    """
    Валидирует вход, пытается выставить ордер и возвращает dict:
    - {'status':'ok','detail':'order_id=...'}
    - {'status':'error','detail':'<текст ошибки>'}
    """
    try:
        sig = Signal(**data)
        order = place_limit_order(
            symbol=sig.symbol,
            side=sig.side,
            price=sig.price,
            quantity=sig.quantity
        )
        return {"status": "ok", "detail": f"order_id={order.get('orderId')}"}

    except BinanceAPIException as e:
        return {"status": "error", "detail": f"BinanceAPI: {e.message}"}

    except ValueError as e:
        # сюда попадают ошибки PRICE_FILTER и валидатора side
        return {"status": "error", "detail": str(e)}

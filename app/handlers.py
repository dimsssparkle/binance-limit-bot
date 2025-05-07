"""
app/handlers.py

Бизнес-логика обработки сигналов:
- Поддержка "флипа" позиции: закрытие противоположной позиции и открытие новой.
- Оба ордера выставляются в Post-Only режиме с maker-комиссией.
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import (
    get_position_amount,
    place_post_only_order,
    place_post_only_exit
)

class Signal(BaseModel):
    """
    Модель сигнала TradingView:
    - symbol: торговая пара, например 'ETHUSDT'
    - side: 'BUY' или 'SELL' (направление новой позиции)
    - quantity: объём в контрактных единицах
    """
    symbol: str
    side: str
    quantity: float

    @validator("side")
    def validate_side(cls, v):
        v_up = v.upper()
        if v_up not in ("BUY", "SELL"):
            raise ValueError("Поле 'side' должно быть 'BUY' или 'SELL'")
        return v_up


def handle_signal(data: dict) -> dict:
    """
    1. Валидирует сигнал.
    2. Проверяет текущую позицию:
       - Если есть позиция в обратном направлении, закрывает её.
    3. Открывает новую позицию в Post-Only режиме.
    4. Возвращает статус.
    """
    try:
        sig = Signal(**data)

        # 1) Текущий объём позиции по symbol
        current_amt = get_position_amount(sig.symbol)

        # 2) Если есть позиция в обратную сторону, закрываем
        if current_amt < 0 and sig.side == "BUY":
            # Закрытие short
            place_post_only_exit(sig.symbol, "SELL", abs(current_amt))
        elif current_amt > 0 and sig.side == "SELL":
            # Закрытие long
            place_post_only_exit(sig.symbol, "BUY", current_amt)

        # 3) Открытие новой позиции
        order = place_post_only_order(
            symbol=sig.symbol,
            side=sig.side,
            quantity=sig.quantity
        )

        return {"status": "ok", "detail": f"order_id={order.get('orderId')}"}

    except BinanceAPIException as e:
        return {"status": "error", "detail": f"BinanceAPI: {e.message}"}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
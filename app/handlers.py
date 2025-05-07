"""
app/handlers.py

Бизнес-логика обработки сигналов:
- Валидация входа через Pydantic.
- Вызов клиента Binance для выставления лимитного ордера.
- Обработка ошибок и возврат стандартизированных результатов.
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import place_limit_order

class Signal(BaseModel):
    """
    Модель сигнала от TradingView:
    - symbol: валютная пара (например, 'ETHUSDT')
    - side: 'BUY' или 'SELL'
    - price: цена лимитного ордера
    - quantity: количество
    """
    symbol: str
    side: str
    price: float
    quantity: float

    @validator("side")
    def side_must_be_valid(cls, v):
        v_up = v.upper()
        if v_up not in ("BUY", "SELL"):
            raise ValueError("Поле 'side' должно быть 'BUY' или 'SELL'")
        return v_up


def handle_signal(data: dict) -> dict:
    """
    Обрабатывает словарь данных сигнала:
    1. Валидирует через Signal.
    2. Пытается выставить ордер через place_limit_order.
    3. Возвращает {'status':'ok','detail':'order_id=...'} или {'status':'error','detail':msg}.
    """
    try:
        # 1) Валидация входных данных
        sig = Signal(**data)
        # 2) Выставление ордера на Binance
        order = place_limit_order(
            symbol=sig.symbol,
            side=sig.side,
            price=sig.price,
            quantity=sig.quantity
        )
        return {"status": "ok", "detail": f"order_id={order.get('orderId')}"}

    except BinanceAPIException as e:
        # Ошибки API Binance (например, несоответствие фильтрам)
        return {"status": "error", "detail": f"BinanceAPI: {e.message}"}
    except ValueError as e:
        # Ошибки валидации (payload или PRICE_FILTER)
        return {"status": "error", "detail": str(e)}
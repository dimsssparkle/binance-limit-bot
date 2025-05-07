"""
app/handlers.py

Бизнес-логика обработки сигналов:
- Не требует передачи цены из TradingView.
- Использует функцию calculate_entry_price + Post-Only ордер.
"""

from pydantic import BaseModel, validator
from binance.exceptions import BinanceAPIException
from app.binance_client import place_post_only_order

class Signal(BaseModel):
    """
    Модель сигнала от TradingView:
    - symbol: валютная пара, например 'ETHUSDT'
    - side: 'BUY' или 'SELL'
    - quantity: объём ордера
    """
    symbol: str
    side: str
    quantity: float

    @validator("side")
    def side_must_be_valid(cls, v):
        v_up = v.upper()
        if v_up not in ("BUY", "SELL"):
            raise ValueError("Поле 'side' должно быть 'BUY' или 'SELL'")
        return v_up


def handle_signal(data: dict) -> dict:
    """
    Обрабатывает сигнал:
    1. Валидирует payload без поля price.
    2. Выставляет Post-Only ордер по вычисленной цене.
    3. Возвращает {'status':'ok','detail':'order_id=...'} или
       {'status':'error','detail':'сообщение об ошибке'}.
    """
    try:
        # 1) Валидация входных данных
        sig = Signal(**data)

        # 2) Выставление ордера: цена рассчитывается внутри
        order = place_post_only_order(
            symbol=sig.symbol,
            side=sig.side,
            quantity=sig.quantity
        )

        # 3) Формируем успешный ответ
        return {"status": "ok", "detail": f"order_id={order.get('orderId')}"}

    except BinanceAPIException as e:
        # Ошибки Binance API
        return {"status": "error", "detail": f"BinanceAPI: {e.message}"}

    except ValueError as e:
        # Ошибки валидации или получения фильтров
        return {"status": "error", "detail": str(e)}
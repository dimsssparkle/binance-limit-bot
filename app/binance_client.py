"""
app/binance_client.py

Обёртка над Binance Futures API:
- Получает фильтры обмена (exchangeInfo) для проверки цены.
- Выставляет лимитный ордер с GTC (Good 'Til Cancelled).
"""

from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

# Инициализируем клиента Binance один раз
_client = Client(settings.binance_api_key, settings.binance_api_secret)


def get_price_filter(symbol: str) -> dict:
    """
    Возвращает блок PRICE_FILTER для заданного символа:
    содержит minPrice, maxPrice, tickSize и т. д.
    """
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return f
    raise ValueError(f"Нет PRICE_FILTER для символа {symbol}")


def place_limit_order(symbol: str, side: str, price: float, quantity: float) -> dict:
    """
    Проверяет, что price входит в [minPrice, maxPrice],
    и выставляет LIMIT-ордер. Возвращает ответ API.
    """
    # Проверка диапазона цены
    pf = get_price_filter(symbol)
    min_p, max_p = float(pf["minPrice"]), float(pf["maxPrice"])
    if not (min_p <= price <= max_p):
        raise ValueError(f"Цена {price} вне диапазона [{min_p}, {max_p}]")

    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTC",
        "price": str(price),
        "quantity": str(quantity),
    }
    try:
        return _client.futures_create_order(**params)
    except BinanceAPIException:
        # Исключение прокидывается дальше в handlers
        raise
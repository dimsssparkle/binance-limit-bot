# app/binance_client.py

from binance.client import Client
from app.config import settings

# Инициализируем Binance-клиент
_client = Client(settings.binance_api_key, settings.binance_api_secret)

def place_limit_order(symbol: str, side: str, price: float, quantity: float) -> dict:
    """
    Выставляет лимитный ордер в Binance Futures и возвращает ответ API.
    """
    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTC",
        "price": str(price),
        "quantity": str(quantity),
    }
    return _client.futures_create_order(**params)

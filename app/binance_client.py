# app/binance_client.py

from binance.client import Client
from binance.exceptions import BinanceAPIException
from app.config import settings

# Инициализируем Binance-клиент
_client = Client(settings.binance_api_key, settings.binance_api_secret)

def get_price_filter(symbol: str) -> dict:
    """
    Берёт из API Binance INFO фильтр цен для символа,
    возвращает dict вида {"minPrice": str, "maxPrice": str, ...}.
    """
    info = _client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return f
    raise ValueError(f"No PRICE_FILTER for symbol {symbol}")

def place_limit_order(symbol: str, side: str, price: float, quantity: float) -> dict:
    """
    Проверяет, что price попадает в диапазон [minPrice, maxPrice],
    и только потом выставляет LIMIT-ордер.
    """
    pf = get_price_filter(symbol)
    min_p = float(pf["minPrice"])
    max_p = float(pf["maxPrice"])
    if not (min_p <= price <= max_p):
        raise ValueError(f"Price {price} out of range [{min_p}, {max_p}]")

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
    except BinanceAPIException as e:
        # Можно ещё логировать e or e.status_code/e.message
        raise


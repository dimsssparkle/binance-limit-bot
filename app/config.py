import os
from dotenv import load_dotenv

if os.getenv("FLASK_ENV", "").lower() == "development":
    load_dotenv(".env")

class Settings:
    binance_api_key: str = os.environ["BINANCE_API_KEY"]
    binance_api_secret: str = os.environ["BINANCE_API_SECRET"]
    webhook_secret: str = os.environ["WEBHOOK_SECRET"]

    telegram_token: str    = os.environ["TELEGRAM_TOKEN"]

    symbols: list[str]     = os.environ.get("SYMBOLS", "ETHUSDT,BTCUSDT").split(",")
    default_symbol: str    = os.environ.get("DEFAULT_SYMBOL", symbols[0])
    default_quantity: float= float(os.environ.get("DEFAULT_QUANTITY", "0.01"))

    flask_env: str = os.environ.get("FLASK_ENV", "production")
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8000"))

settings = Settings()

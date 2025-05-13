"""
app/config.py

Модуль отвечает за загрузку и предоставление всех настроек приложения.
В режиме разработки читает переменные из файла .env, в продакшене — из системных переменных окружения.
"""
import os
from dotenv import load_dotenv

if os.getenv("FLASK_ENV", "").lower() == "development":
    load_dotenv(".env")

class Settings:
    binance_api_key: str = os.environ["BINANCE_API_KEY"]
    binance_api_secret: str = os.environ["BINANCE_API_SECRET"]
    webhook_secret: str = os.environ["WEBHOOK_SECRET"]
    flask_env: str = os.environ.get("FLASK_ENV", "production")
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8000"))

settings = Settings()
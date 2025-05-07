"""
app/config.py

Модуль отвечает за загрузку и предоставление всех настроек приложения.
В режиме разработки читает переменные из файла .env, в продакшене — из системных переменных окружения.
"""

import os
from dotenv import load_dotenv

# В режиме разработки (.env) подгружаем переменные в os.environ
if os.getenv("FLASK_ENV", "").lower() == "development":
    load_dotenv(".env")  # загружаем .env при FLASK_ENV=development

class Settings:
    """
    Класс группирует все настройки приложения.
    Обязательные параметры читаются через os.environ[...] и при их отсутствии возбуждается KeyError.
    Необязательные параметры имеют значения по умолчанию.
    """

    # Обязательные параметры
    binance_api_key: str = os.environ["BINANCE_API_KEY"]       # API-ключ Binance
    binance_api_secret: str = os.environ["BINANCE_API_SECRET"] # Секретный ключ Binance
    webhook_secret: str = os.environ["WEBHOOK_SECRET"]         # Секрет для проверки webhook

    # Необязательные параметры
    flask_env: str = os.environ.get("FLASK_ENV", "production") # Окружение: development или production
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")       # Уровень логирования
    host: str = os.environ.get("HOST", "0.0.0.0")               # Адрес для bind Flask
    port: int = int(os.environ.get("PORT", "8000"))             # Порт для bind Flask

# Единый экземпляр настроек для всего приложения
settings = Settings()
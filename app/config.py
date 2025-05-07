import os
from pydantic import BaseSettings, Field

# Загружаем .env только в режиме разработки
if os.getenv("FLASK_ENV", "").lower() == "development":
    from dotenv import load_dotenv  # локальная зависимость
    load_dotenv(".env")

class Settings(BaseSettings):
    binance_api_key: str = Field(..., env="BINANCE_API_KEY")
    binance_api_secret: str = Field(..., env="BINANCE_API_SECRET")
    webhook_secret: str = Field(..., env="WEBHOOK_SECRET")
    flask_env: str = Field("production", env="FLASK_ENV")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    host: str = Field("0.0.0.0", env="HOST")
    port: int = Field(8000, env="PORT")

    class Config:
        # прочие опции Pydantic — не указываем env_file,
        # чтобы в проде получали напрямую из os.environ
        case_sensitive = True

settings = Settings()

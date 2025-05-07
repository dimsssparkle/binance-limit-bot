# app/config.py

from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    binance_api_key: str = Field(..., env="BINANCE_API_KEY")
    binance_api_secret: str = Field(..., env="BINANCE_API_SECRET")
    webhook_secret: str = Field(..., env="WEBHOOK_SECRET")
    flask_env: str = Field("production", env="FLASK_ENV")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    host: str = Field("0.0.0.0", env="HOST")
    port: int = Field(8000, env="PORT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()

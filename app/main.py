# app/main.py

from flask import Flask, request, abort, jsonify
import logging
from pathlib import Path
from app.config import settings
from app.handlers import handle_signal
from app.binance_client import init_data

# 1) Настраиваем логирование до всего, чтобы ловить сообщения init_data()
logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)

# 2) Инициализация папки data и CSV-файлов
init_data()
logger.info(f"Data directory initialized at: {Path(__file__).resolve().parent.parent / 'data'}")

# 3) Создаем Flask-приложение
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    logger.info("Received webhook")
    # Проверяем секрет в query
    secret_qs = request.args.get("secret", "")
    if secret_qs != settings.webhook_secret:
        abort(401, "Invalid webhook secret (query)")

    # Проверяем секрет в теле
    data = request.get_json(force=True)
    if data.get("secret") != settings.webhook_secret:
        abort(401, "Invalid webhook secret (body)")

    data.pop("secret", None)
    result = handle_signal(data)
    status_code = 200 if result.get("status") == "ok" else 400
    logger.info(f"Response: {result}")
    return jsonify(result), status_code

if __name__ == "__main__":
    # Для локального запуска: покажем текущую директорию
    cwd = Path().resolve()
    logger.info(f"Starting locally, CWD: {cwd}")
    app.run(host=settings.host, port=settings.port)

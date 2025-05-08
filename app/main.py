# app/main.py

from flask import Flask, request, abort, jsonify
import logging
from pathlib import Path

from app.config import settings
from app.handlers import handle_signal
from app.binance_client import init_data
from app.websocket_manager import start_websocket

# 1) Инициализация папки и файлов для истории
init_data()

# 2) Запуск WebSocket для указанных пар
# Замените список на ваши активы, например ['ETHUSDT', 'BTCUSDT']
start_websocket(['ETHUSDT'])

# 3) Настройка логирования
logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)

# 4) Логируем путь к каталогу данных
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
logger.info(f"Data directory initialized at: {DATA_DIR}")

# 5) Создание Flask-приложения
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    logger.info("Received webhook request")
    # Проверяем секрет в query-параметре
    secret_qs = request.args.get("secret", "")
    if secret_qs != settings.webhook_secret:
        abort(401, "Invalid webhook secret (query)")

    # Проверяем секрет в теле запроса
    data = request.get_json(force=True)
    if data.get("secret") != settings.webhook_secret:
        abort(401, "Invalid webhook secret (body)")

    # Убираем секрет из данных и обрабатываем сигнал
    data.pop("secret", None)
    result = handle_signal(data)
    status_code = 200 if result.get("status") == "ok" else 400

    logger.info(f"Response: {result}")
    return jsonify(result), status_code

@app.route("/debug/files", methods=["GET"])
def debug_files():
    """
    Эндпоинт для отладки: возвращает список файлов в папке data.
    """
    try:
        files = [p.name for p in DATA_DIR.iterdir()]
        return jsonify({"data_dir": str(DATA_DIR), "files": files}), 200
    except Exception as e:
        logger.error(f"Error reading data dir: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    cwd = Path().resolve()
    logger.info(f"Starting locally, CWD: {cwd}")
    app.run(host=settings.host, port=settings.port)

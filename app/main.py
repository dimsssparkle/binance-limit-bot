# app/main.py
from flask import Flask, request, abort, jsonify, send_from_directory
from flask_sock import Sock
import logging
from pathlib import Path
import os

from app.config import settings
from app.handlers import handle_signal
from app.binance_client import init_data
from app.websocket_manager import start_websocket

# Инициализируем данные и запускаем сборщик стакана
init_data()
start_websocket(['ETHUSDT'])

logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)

# директории
BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, 'static')
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
logger.info(f"Data directory initialized at: {DATA_DIR}")

# Фласк-приложение
app = Flask(
    __name__,
    static_folder=STATIC_DIR,
    static_url_path='/static'
)
sock = Sock(app)

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

@app.route("/webhook", methods=["POST"])
def webhook():
    logger.info("Received webhook request")
    secret_qs = request.args.get("secret", "")
    if secret_qs != settings.webhook_secret:
        abort(401, "Invalid webhook secret (query)")
    data = request.get_json(force=True)
    if data.get("secret") != settings.webhook_secret:
        abort(401, "Invalid webhook secret (body)")
    data.pop("secret", None)
    result = handle_signal(data)
    status_code = 200 if result.get("status") == "ok" else 400
    logger.info(f"Response: {result}")
    return jsonify(result), status_code

@app.route("/debug/files", methods=["GET"])
def debug_files():
    try:
        files = [p.name for p in DATA_DIR.iterdir()]
        return jsonify({"data_dir": str(DATA_DIR), "files": files}), 200
    except Exception as e:
        logger.error(f"Error reading data dir: {e}")
        return jsonify({"error": str(e)}), 500

@sock.route("/ws")
def websocket(ws):
    logger.info("WebSocket connected")
    # Пример: шлём клиенту обновления стакана
    while True:
        book = get_order_book_snapshot()  # ваша функция из websocket_manager
        ws.send_json(book)
        # небольшой сон, чтобы снизить CPU
        import gevent
        gevent.sleep(0.1)

if __name__ == "__main__":
    cwd = Path().resolve()
    logger.info(f"Starting locally, CWD: {cwd}")
    app.run(host=settings.host, port=settings.port)

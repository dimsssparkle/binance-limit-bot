# File: app/main.py
"""
Основной модуль Flask-приложения без WebSocket-ендпоинтов и операций с диском.
"""
import logging
import os
import json
import time

from flask import Flask, request, abort, jsonify, send_from_directory

from app.config import settings
from app.handlers import handle_signal
from app.binance_client import _client

# Логирование
logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), 'static'), static_url_path='/static')

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

@app.route("/webhook", methods=["POST"])
def webhook():
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

@app.route("/api/orderbook", methods=["GET"])
def api_orderbook():
    try:
        resp = _client.futures_order_book(symbol="ETHUSDT", limit=20)
        data = {"bids": resp.get("bids", []), "asks": resp.get("asks", [])}
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Failed to fetch order book via REST: {e}")
        return jsonify({"bids": [], "asks": []}), 500

if __name__ == "__main__":
    app.run(host=settings.host, port=settings.port)

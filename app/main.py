# app/main.py

from flask import Flask, request, abort, jsonify
import logging
from app.config import settings
from app.handlers import handle_signal
from app.binance_client import init_data

# инициализируем папку data и файлы истории
init_data()

# настраиваем логирование
logging.basicConfig(level=settings.log_level)
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    logging.info("Received webhook")
    secret_qs = request.args.get("secret", "")
    if secret_qs != settings.webhook_secret:
        abort(401, "Invalid webhook secret (query)")

    data = request.get_json(force=True)
    if data.get("secret") != settings.webhook_secret:
        abort(401, "Invalid webhook secret (body)")

    data.pop("secret", None)
    result = handle_signal(data)
    status_code = 200 if result.get("status") == "ok" else 400
    logging.info(f"Response: {result}")
    return jsonify(result), status_code

if __name__ == "__main__":
    app.run(host=settings.host, port=settings.port)

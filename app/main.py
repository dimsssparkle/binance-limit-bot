# app/main.py

from flask import Flask, request, abort, jsonify
import logging
from app.config import settings
from app.handlers import handle_signal

# Настройка логирования
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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

    logger.info(f"Result: {result}")
    status_code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), status_code

if __name__ == "__main__":
    app.run(host=settings.host, port=settings.port)
# app/main.py

from flask import Flask, request, abort, jsonify
from app.config import settings
from app.handlers import handle_signal

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    # проверка секрета в query-параметре
    if request.args.get("secret", "") != settings.webhook_secret:
        abort(401, "Invalid webhook secret")

    data = request.get_json(force=True)
    if data.get("secret") != settings.webhook_secret:
        abort(401, "Invalid webhook secret")

    data.pop("secret", None)
    result = handle_signal(data)

    if result["status"] == "error":
        return jsonify(result), 400
    return jsonify(result), 200

if __name__ == "__main__":
    app.run(host=settings.host, port=settings.port)

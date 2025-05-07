from flask import Flask, request, abort, jsonify
from app.config import settings
from app.handlers import handle_signal

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    # 1) Проверяем секрет в query-параметре
    secret_qs = request.args.get("secret", "")
    if secret_qs != settings.webhook_secret:
        abort(401, "Invalid webhook secret (query)")

    # 2) Парсим JSON и проверяем секрет в теле
    data = request.get_json(force=True)
    if data.get("secret") != settings.webhook_secret:
        abort(401, "Invalid webhook secret (body)")

    # 3) Убираем секрет из данных и передаём остальное в обработчик
    data.pop("secret", None)
    result = handle_signal(data)

    # 4) Отдаём ответ
    return jsonify({"status": "ok", "detail": result}), 200

if __name__ == "__main__":
    app.run(host=settings.host, port=settings.port)

"""
app/main.py

Основной входной файл Flask-приложения:
- Определяет endpoint `/webhook` для приёма сигналов из TradingView.
- Проверяет секрет для защиты от неавторизованных запросов.
- Делегирует бизнес-логику в `handle_signal` и возвращает JSON-ответ.
"""

from flask import Flask, request, abort, jsonify
from app.config import settings
from app.handlers import handle_signal

# Создаём Flask-приложение
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Обработчик POST /webhook:
    1. Проверяет секрет из query-параметра.
    2. Загружает JSON и снова проверяет секрет из тела.
    3. Удаляет поле secret из данных и передаёт их в бизнес-логику.
    4. Возвращает HTTP 200 при успехе или 400 при ошибке.
    """
    # 1) Проверка секретного ключа в параметре URL
    if request.args.get("secret", "") != settings.webhook_secret:
        abort(401, "Invalid webhook secret")

    # 2) Получаем JSON из тела и проверяем поле secret
    data = request.get_json(force=True)
    if data.get("secret") != settings.webhook_secret:
        abort(401, "Invalid webhook secret")

    # 3) Убираем секретное поле перед обработкой
    data.pop("secret", None)
    result = handle_signal(data)

    # 4) Возвращаем ответ в формате JSON
    if result["status"] == "error":
        return jsonify(result), 400
    return jsonify(result), 200

if __name__ == "__main__":
    # Запуск встроенного сервера Flask (только для разработки)
    app.run(host=settings.host, port=settings.port)
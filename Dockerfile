# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Сначала ставим зависимости
COPY requirements.txt .
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install --no-cache-dir -r requirements.txt

# Копируем весь код
COPY . .

# Открываем порт
EXPOSE 8000

# Запускаем Gunicorn с правильным воркером и без таймаута,
# плюс в фоне Telegram-бота
CMD sh -c "\
    gunicorn \
      --bind 0.0.0.0:8000 \
      --worker-class gthread \
      --threads 4 \
      --timeout 0 \
      app.main:app \
    & \
    python3 -m app.telegram_bot \
"

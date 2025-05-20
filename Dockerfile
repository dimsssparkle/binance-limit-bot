# Dockerfile

FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD sh -c "\
    gunicorn \
      --bind 0.0.0.0:8000 \
      --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
      --timeout 0 \
      app.main:app \
    & \
    python3 -m app.telegram_bot \
"

# Dockerfile

# 1) Базовый образ с Python 3.11
FROM python:3.11-slim

# 2) Задаём рабочую директорию
WORKDIR /app

# 3) Копируем файл зависимостей
COPY requirements.txt .

# 4) Устанавливаем зависимости
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install --no-cache-dir -r requirements.txt

# 5) Копируем весь код приложения
COPY . .

# 6) Открываем порт для HTTP
EXPOSE 8000

# 7) Запускаем приложение через Gunicorn
CMD ["python3", "-m", "gunicorn", "--bind", "0.0.0.0:8000", "app.main:app"]
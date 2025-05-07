# Используем облегчённый образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Обновляем pip и устанавливаем все зависимости из requirements.txt
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install --no-cache-dir -r requirements.txt

# Копируем весь код приложения
COPY . .

# Пробрасываем порт
EXPOSE 8000

# Запускаем через Gunicorn как модуль Python
CMD ["python3", "-m", "gunicorn", "--bind", "0.0.0.0:8000", "app.main:app"]

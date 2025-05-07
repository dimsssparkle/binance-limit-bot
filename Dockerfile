# Используем лёгкий образ Python
FROM python:3.11-slim

# Каталог приложения
WORKDIR /app

# Сначала копируем зависимости и ставим их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код
COPY . .

# Открываем порт (должен совпадать с settings.PORT)
EXPOSE 8000

# Запускаем через Gunicorn для продакшена
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app.main:app"]

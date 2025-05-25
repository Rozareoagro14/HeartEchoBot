FROM python:3.9-slim

WORKDIR /app

# Установка git (чтобы клонировать репозиторий)
RUN apt-get update && apt-get install -y git

# Клонирование репозитория (замените URL на актуальный, если необходимо)
RUN git clone https://github.com/Rozareoagro14/HeartEchoBot.git /app

# Установка зависимостей из requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Запуск бота через main.py
CMD ["python", "main.py"] 
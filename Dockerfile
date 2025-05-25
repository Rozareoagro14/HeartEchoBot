FROM python:3.9-slim

WORKDIR /app

# Копируем файлы проекта (кроме .env, чтобы не засорять образ секретами)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Примечание: файл .env (с BOT_TOKEN) должен передаваться при запуске контейнера (например, через --env-file .env)
CMD ["python", "__main__.py"] 
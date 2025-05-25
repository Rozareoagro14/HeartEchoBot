# HeartEchoBot

Этот проект представляет собой Telegram-бота (HeartEchoBot) для просмотра сериалов.  
Исходный код скачивается с GitHub (репозиторий пользователя Rozareoagro14).  
Структура проекта (после клонирования):
```
C:\Bots\HeartEchoBot
├── app
│   ├── __init__.py (пустой)
│   ├── bot.py (логика бота, импорты, запуск polling)
│   ├── config.py (настройки, например, токен бота)
│   ├── handlers.py (обработчики команд)
│   └── films.db (база данных)
├── main.py (точка входа, запускает бота)
├── requirements.txt (зависимости)
├── Dockerfile (инструкции для сборки контейнера, скачивающие исходники с GitHub)
└── README.md (этот файл)
```

## Запуск бота локально

1. Убедитесь, что у вас установлен Python (например, версии 3.9) и pip.
2. Склонируйте репозиторий (или скачайте исходники) с GitHub:
   ```sh
   git clone https://github.com/Rozareoagro14/HeartEchoBot.git
   cd HeartEchoBot
   ```
3. Установите зависимости:
   ```sh
   pip install -r requirements.txt
   ```
4. Запустите бота:
   ```sh
   python main.py
   ```

## Запуск бота в Docker-контейнере (на виртуальном сервере)

1. Убедитесь, что на сервере установлен Docker (например, Docker Engine).
2. Склонируйте репозиторий (или скачайте исходники) с GitHub:
   ```sh
   git clone https://github.com/Rozareoagro14/HeartEchoBot.git
   cd HeartEchoBot
   ```
3. Перейдите в корень проекта (там, где лежит Dockerfile) и соберите образ (Dockerfile скачает исходники с GitHub):
   ```sh
   docker build -t heartechobot .
   ```
4. Запустите контейнер (например, в фоновом режиме):
   ```sh
   docker run -d --name heartechobot heartechobot
   ```
   (Если необходимо передать переменные окружения, например, токен бота, используйте флаг –env или –env-file.)

Если появятся ошибки или вопросы, обратитесь к логам контейнера (например, docker logs heartechobot) или к документации Docker. 
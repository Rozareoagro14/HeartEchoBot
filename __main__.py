print("Бот запускается...")
import asyncio
from app.bot import bot, dp, startup

async def main():
    try:
        await startup()
        await dp.start_polling(bot)
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 
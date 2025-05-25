print("Бот запускается...")
import asyncio
from app.bot import bot, dp, startup

async def main():
    await startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
import os
import asyncio
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")
    if not CHAT_ID:
        raise ValueError("SIGNAL_CHAT_ID is missing")

    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(
        chat_id=int(CHAT_ID),
        text="🚀 Quiet Alpha Bot is LIVE!"
    )

if __name__ == "__main__":
    asyncio.run(main())

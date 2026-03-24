import os
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGNAL_CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

bot = Bot(token=BOT_TOKEN)

if __name__ == "__main__":
    if ADMIN_CHAT_ID:
        bot.send_message(chat_id=ADMIN_CHAT_ID, text="Quiet Alpha System Online ✅")
    if SIGNAL_CHAT_ID:
        bot.send_message(chat_id=SIGNAL_CHAT_ID, text="Quiet Alpha Signal Channel Connected ✅")
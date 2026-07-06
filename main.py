from flask import Flask, request
import os
import requests
from datetime import datetime

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not CHAT_ID:
    raise ValueError("SIGNAL_CHAT_ID is missing")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send_message(text):
    requests.post(
        TELEGRAM_URL,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
    )


@app.route("/")
def home():
    return "Quiet Alpha Bot Online"


@app.route("/call", methods=["POST"])
def call_signal():

    data = request.get_json()

    strike = data.get("strike")
    premium = data.get("premium")

    today = datetime.now().strftime("%d %b %Y")

    message = f"""
🟢 <b>CALL SIGNAL</b>

📍 Strike : {strike}C
💰 Premium : ${premium}
📅 Date : {today}
"""

    send_message(message)

    return {"status": "success"}


@app.route("/put", methods=["POST"])
def put_signal():

    data = request.get_json()

    strike = data.get("strike")
    premium = data.get("premium")

    today = datetime.now().strftime("%d %b %Y")

    message = f"""
🔴 <b>PUT SIGNAL</b>

📍 Strike : {strike}P
💰 Premium : ${premium}
📅 Date : {today}
"""

    send_message(message)

    return {"status": "success"}


if name == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )

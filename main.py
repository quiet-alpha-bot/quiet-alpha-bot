from flask import Flask, request
import os
import requests
from datetime import datetime

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

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
    return "Quiet Alpha Bot Running"


@app.route("/call", methods=["POST"])
def call():
    data = request.json

    strike = data["strike"]
    premium = data["premium"]

    today = datetime.now().strftime("%d %b %Y")

    msg = f"""
🟢 <b>CALL SIGNAL</b>

📍 Strike : {strike}C
💰 Premium : ${premium}
📅 Date : {today}
"""

    send_message(msg)

    return {"status": "ok"}


@app.route("/put", methods=["POST"])
def put():
    data = request.json

    strike = data["strike"]
    premium = data["premium"]

    today = datetime.now().strftime("%d %b %Y")

    msg = f"""
🔴 <b>PUT SIGNAL</b>

📍 Strike : {strike}P
💰 Premium : ${premium}
📅 Date : {today}
"""

    send_message(msg)

    return {"status": "ok"}


if name == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

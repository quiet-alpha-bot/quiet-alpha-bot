from flask import Flask, request
import os
import requests
from datetime import datetime

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

if BOT_TOKEN is None:
    raise Exception("BOT_TOKEN is missing")

if CHAT_ID is None:
    raise Exception("SIGNAL_CHAT_ID is missing")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send_message(text):
    requests.post(
        TELEGRAM_API,
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
def call():

    data = request.get_json(force=True)

    strike = data["strike"]
    premium = data["premium"]

    today = datetime.now().strftime("%d %b %Y")

    send_message(
        f"""🟢 <b>CALL SIGNAL</b>

Strike : {strike}C
Premium : ${premium}
Date : {today}
"""
    )

    return {"status": "ok"}


@app.route("/put", methods=["POST"])
def put():

    data = request.get_json(force=True)

    strike = data["strike"]
    premium = data["premium"]

    today = datetime.now().strftime("%d %b %Y")

    send_message(
        f"""🔴 <b>PUT SIGNAL</b>

Strike : {strike}P
Premium : ${premium}
Date : {today}
"""
    )

    return {"status": "ok"}


if name == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

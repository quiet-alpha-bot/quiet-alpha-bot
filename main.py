import os
import time
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGNAL_CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": SIGNAL_CHAT_ID, "text": text})

def fetch_uw():
    url = "https://api.unusualwhales.com/api/alerts"
    headers = {"Authorization": f"Bearer {UW_API_KEY}"}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()

        if not data:
            return None

        alert = data[0]

        ticker = alert.get("ticker", "UNKNOWN")
        side = alert.get("type", "UNKNOWN")
        premium = alert.get("premium", 0)

        return ticker, side, premium

    except Exception as e:
        send_telegram(f"UW Error: {e}")
        return None

if __name__ == "__main__":
    send_telegram("🐋 UW bot started")

    while True:
        result = fetch_uw()

        if result:
            ticker, side, premium = result

            text = f"""🐋 UW Signal

📊 {ticker} {side}
💰 Premium: {premium}
"""

            send_telegram(text)

        time.sleep(30)

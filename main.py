import os
import time
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGNAL_CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": SIGNAL_CHAT_ID, "text": text},
            timeout=15
        )
        print("Telegram status:", r.status_code)
        print("Telegram response:", r.text[:300])
    except Exception as e:
        print("Telegram send error:", repr(e))


def fetch_uw():
    url = "https://api.unusualwhales.com/api/option-alerts"
    headers = {
        "Authorization": f"Bearer {UW_API_KEY}",
        "Accept": "application/json"
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)

        print("=" * 80)
        print("UW STATUS:", r.status_code)
        print("UW RAW TEXT:")
        print(r.text[:1500])
        print("=" * 80)

        try:
            data = r.json()
            print("UW JSON TYPE:", type(data))
            print("UW JSON PREVIEW:", str(data)[:1500])
        except Exception as json_error:
            print("UW JSON PARSE ERROR:", repr(json_error))

        return None

    except Exception as e:
        print("UW EXCEPTION:", repr(e))
        return None


if __name__ == "__main__":
    print("🐋 UW debug bot started")
    send_telegram("🐋 UW debug bot started")

    while True:
        try:
            fetch_uw()
        except Exception as e:
            print("MAIN LOOP ERROR:", repr(e))

        time.sleep(30)

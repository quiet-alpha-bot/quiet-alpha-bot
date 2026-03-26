import os
import time
import imaplib
import email
import requests

# =========================
# 🔐 Environment Variables
# =========================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

UW_API_KEY = os.getenv("UW_API_KEY")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "15"))

# =========================
# 📩 Telegram Sender
# =========================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": msg
    }
    r = requests.post(url, data=data)
    print("Telegram status:", r.status_code)
    print("Telegram response:", r.text)

# =========================
# 📧 TradingView Email Reader
# =========================
def check_email():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN)')
        mail_ids = messages[0].split()

        for num in mail_ids[-5:]:
            status, data = mail.fetch(num, '(RFC822)')
            msg = email.message_from_bytes(data[0][1])

            subject = msg["subject"]

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="ignore")
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            text = f"🔥 Quiet Alpha Signal\n\n📩 {subject}\n\n{body[:300]}"
            send_telegram(text)

        mail.logout()

    except Exception as e:
        print("Email Error:", e)

# =========================
# 🐋 Unusual Whales Fetch
# =========================
def check_uw():
    try:
        url = "https://api.unusualwhales.com/api/alerts"  # ✅ FIXED

        headers = {
            "Authorization": f"Bearer {UW_API_KEY}"
        }

        r = requests.get(url, headers=headers)

        print("UW STATUS:", r.status_code)
        print("UW RAW TEXT:", r.text[:500])

        if r.status_code != 200:
            send_telegram(f"⚠️ UW ERROR: {r.status_code}")
            return

        data = r.json()

        if isinstance(data, dict) and "data" in data:
            alerts = data["data"]
        else:
            alerts = data

        if not alerts:
            print("No UW alerts")
            return

        for alert in alerts[:3]:
            symbol = alert.get("symbol", "N/A")
            strike = alert.get("strike", "N/A")
            option_type = alert.get("type", "N/A")
            premium = alert.get("premium", "N/A")

            msg = f"""🐋 UW FLOW

📊 {symbol}
🎯 Strike: {strike}
📌 Type: {option_type}
💰 Premium: {premium}
"""
            send_telegram(msg)

    except Exception as e:
        print("UW Error:", e)
        send_telegram(f"❌ UW Exception: {str(e)}")

# =========================
# 🚀 Main Loop
# =========================
if __name__ == "__main__":
    send_telegram("✅ Quiet Alpha bot started")

    while True:
        check_email()
        check_uw()
        time.sleep(POLL_SECONDS)

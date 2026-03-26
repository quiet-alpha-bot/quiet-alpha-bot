import os
import re
import time
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import requests

# ========= ENV =========
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))

# ========= MEMORY =========
latest_tv_signal = None
seen_subjects = set()
sent_contracts = {}

# ========= TELEGRAM =========
def send(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

# ========= HELPERS =========
def now():
    return datetime.utcnow()

def clean_old():
    cutoff = now() - timedelta(minutes=10)
    for k in list(sent_contracts):
        if sent_contracts[k] < cutoff:
            del sent_contracts[k]

def sent_before(contract):
    clean_old()
    return contract in sent_contracts

def mark(contract):
    sent_contracts[contract] = now()

def decode(val):
    if not val:
        return ""
    parts = []
    for p, enc in decode_header(val):
        if isinstance(p, bytes):
            parts.append(p.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(p)
    return "".join(parts)

def extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
    return msg.get_payload(decode=True).decode(errors="ignore")

# ========= TV EMAIL =========
def check_tv():
    global latest_tv_signal

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        _, data = mail.search(None, "ALL")
        ids = data[0].split()[-5:]

        for i in reversed(ids):
            _, msg_data = mail.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            subject = decode(msg.get("Subject"))
            body = extract_body(msg)

            if subject in seen_subjects:
                continue

            if "CALL" in body.upper():
                side = "CALL"
            elif "PUT" in body.upper():
                side = "PUT"
            else:
                continue

            price_match = re.search(r"PRICE:\s*(\d+\.?\d*)", body)
            price = price_match.group(1) if price_match else "N/A"

            latest_tv_signal = {
                "side": side,
                "price": price,
                "time": now()
            }

            seen_subjects.add(subject)
            print("TV:", latest_tv_signal)
            break

        mail.logout()

    except Exception as e:
        print("TV error:", e)

# ========= UW =========
def check_uw():
    try:
        url = "https://api.unusualwhales.com/api/alerts"
        headers = {"Authorization": f"Bearer {UW_API_KEY}"}

        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            return

        data = r.json()
        alerts = data.get("data", [])

        for a in alerts[:5]:
            contract = a.get("contract") or ""
            if not contract or sent_before(contract):
                continue

            option_type = a.get("type", "").upper()
            strike = a.get("strike", "N/A")
            premium = a.get("premium", "N/A")

            # ========= NO TV =========
            if not latest_tv_signal:
                msg = f"""🐋 Quiet Alpha Flow

📊 {option_type}
🎯 Strike: {strike}
💰 Premium: {premium}
"""
                send(msg)
                mark(contract)
                break

            # ========= CHECK MATCH =========
            match = latest_tv_signal["side"] == option_type

            if match:
                msg = f"""💎 Quiet Alpha A+ Signal

📊 {option_type}
💰 Entry: {latest_tv_signal["price"]}

🐋 UW Flow
🎯 Strike: {strike}
💰 Premium: {premium}

🔥 TV + UW MATCH
"""
            else:
                msg = f"""🟡 Quiet Alpha Watch

📊 TV: {latest_tv_signal["side"]}
🐋 UW: {option_type}

⚠️ اتجاه مختلف
"""

            send(msg)
            mark(contract)
            break

    except Exception as e:
        print("UW error:", e)

# ========= MAIN =========
if __name__ == "__main__":
    send("🚀 Quiet Alpha V5 bot started")

    while True:
        check_tv()
        check_uw()
        time.sleep(POLL_SECONDS)

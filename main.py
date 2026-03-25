import os
import time
import requests
import imaplib
import email
from datetime import datetime, timedelta

# =============================
# ENV VARIABLES
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

UW_API_KEY = os.getenv("UW_API_KEY")

# =============================
# GLOBAL MEMORY
# =============================
last_tv_signal = None
last_tv_time = None

SYNC_WINDOW = 180  # 3 minutes

trade_counter = 1

# =============================
# TELEGRAM
# =============================
def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text
    }
    requests.post(url, data=data)

# =============================
# PARSE TRADINGVIEW EMAIL
# =============================
def check_tv_email():
    global last_tv_signal, last_tv_time

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")

    status, messages = mail.search(None, '(UNSEEN)')
    mail_ids = messages[0].split()

    for mail_id in mail_ids:
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = msg["subject"]

        if subject:
            subject = subject.upper()

            if "SPX" in subject:
                if "CALL" in subject:
                    last_tv_signal = "CALL"
                    last_tv_time = datetime.utcnow()
                    print("TV SIGNAL: CALL")

                elif "PUT" in subject:
                    last_tv_signal = "PUT"
                    last_tv_time = datetime.utcnow()
                    print("TV SIGNAL: PUT")

    mail.logout()

# =============================
# FETCH UW DATA
# =============================
def get_uw_flow():
    url = "https://api.unusualwhales.com/api/option-trades/flow-alerts"
    headers = {
        "Authorization": f"Bearer {UW_API_KEY}"
    }

    try:
        response = requests.get(url, headers=headers)
        data = response.json()

        if not data:
            return None

        trade = data[0]

        ticker = trade.get("ticker")
        option_type = trade.get("option_type")
        premium = trade.get("premium", 0)

        if ticker != "SPX":
            return None

        if premium < 100000:
            return None

        signal = "CALL" if option_type == "call" else "PUT"

        return {
            "signal": signal,
            "premium": premium
        }

    except Exception as e:
        print("UW ERROR:", e)
        return None

# =============================
# SYNC ENGINE
# =============================
def process_signals():
    global trade_counter

    uw = get_uw_flow()

    if not uw:
        return

    if not last_tv_signal or not last_tv_time:
        return

    time_diff = (datetime.utcnow() - last_tv_time).total_seconds()

    if time_diff > SYNC_WINDOW:
        return

    if uw["signal"] != last_tv_signal:
        return

    trade_id = f"QA-{trade_counter:03}"
    trade_counter += 1

    text = f"""🔥 Quiet Alpha Signal

{trade_id}
SPX {uw['signal']}

🧠 UW: High Premium Flow
👁️ TV: Confirmed

⚡ Institutional Setup"""

    send_telegram(text)

    print("SENT:", trade_id)

# =============================
# LOOP
# =============================
while True:
    try:
        check_tv_email()
        process_signals()
        time.sleep(20)

    except Exception as e:
        print("ERROR:", e)
        time.sleep(20)

# ===== QUIET ALPHA PRO V8 =====
import os
import re
import time
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import requests

# ===== ENV =====
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")

POLL_SECONDS = 20
DEDUP_MINUTES = 10

# ===== STORAGE =====
recent_contracts = {}
recent_strikes = {}
latest_tv = None
active_trades = {}

# ================= TIME =================
def now():
    return datetime.utcnow()

# ================= TELEGRAM =================
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

# ================= CLEAN DUP =================
def clean():
    cutoff = now() - timedelta(minutes=DEDUP_MINUTES)

    for k in list(recent_contracts):
        if recent_contracts[k] < cutoff:
            del recent_contracts[k]

    for k in list(recent_strikes):
        if recent_strikes[k] < cutoff:
            del recent_strikes[k]

# ================= TV =================
def check_tv():
    global latest_tv

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        _, data = mail.search(None, "ALL")
        ids = data[0].split()[-5:]

        for i in reversed(ids):
            _, msg_data = mail.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            body = ""

            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode()
            else:
                body = msg.get_payload(decode=True).decode()

            if "TradingView" not in str(msg.get("From")):
                continue

            side = re.search(r"SIGNAL:\s*(CALL|PUT)", body)
            price = re.search(r"PRICE:\s*([0-9.]+)", body)

            if side:
                latest_tv = {
                    "side": side.group(1),
                    "price": float(price.group(1)) if price else None,
                    "time": now()
                }

        mail.logout()
    except:
        pass

# ================= UW =================
def get_uw():
    try:
        r = requests.get(
            "https://api.unusualwhales.com/api/alerts",
            headers={"Authorization": f"Bearer {UW_API_KEY}"}
        )
        return r.json().get("data", [])
    except:
        return []

# ================= SIGNAL =================
def check_uw():
    global latest_tv

    alerts = get_uw()

    for a in alerts[:10]:

        contract = a.get("contract", "")
        if not contract:
            continue

        symbol = "SPXW" if "SPX" in contract else "N/A"

        strike_match = re.findall(r"\d{4,5}", contract)
        strike = strike_match[-1] if strike_match else "N/A"

        side = "CALL" if "C" in contract else "PUT"

        clean()

        # ❌ منع تكرار نفس العقد
        if contract in recent_contracts:
            continue

        # ❌ منع تكرار نفس الاسترايك
        key = f"{symbol}_{strike}_{side}"
        if key in recent_strikes:
            continue

        # ❌ لازم TV
        if not latest_tv:
            continue

        if latest_tv["side"] != side:
            continue

        # 🔥 السعر الحقيقي (أهم نقطة)
        entry = a.get("price") or a.get("mark") or latest_tv["price"]

        if not entry:
            continue

        entry = float(entry)

        # 🎯 Targets
        tp1 = round(entry * 1.3, 2)
        tp2 = round(entry * 1.5, 2)
        tp3 = round(entry * 2.0, 2)
        sl = round(entry * 0.65, 2)

        msg = f"""🔥 Quiet Alpha Signal

{symbol} {side}
Strike: {strike}
Entry: {entry}

Confidence: MEDIUM
Score: 65/100

🎯 Targets:
TP1: {tp1}
TP2: {tp2}
TP3: {tp3}

⚠️ Stop:
{sl}

🪪 Contract:
{contract}

هذه ليست توصية شراء أو بيع"""

        send(msg)

        # ✅ حفظ الصفقة للتتبع
        active_trades[contract] = {
            "entry": entry,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "time": now()
        }

        recent_contracts[contract] = now()
        recent_strikes[key] = now()

        break

# ================= TRACKING =================
def track_trades():
    global active_trades

    for contract, trade in list(active_trades.items()):

        try:
            r = requests.get(
                f"https://api.unusualwhales.com/api/option/{contract}",
                headers={"Authorization": f"Bearer {UW_API_KEY}"}
            )

            data = r.json()

            price = data.get("mark") or data.get("last")

            if not price:
                continue

            price = float(price)
            entry = trade["entry"]

            change = ((price - entry) / entry) * 100

            # 🎯 TP1
            if change >= 30 and not trade["tp1_hit"]:
                send(f"""🎯 Quiet Alpha Update

📈 Profit: +{round(change,1)}%
💰 Price: {price}

💡 ارفع وقفك
""")
                trade["tp1_hit"] = True

            # 🔥 TP2
            elif change >= 50 and not trade["tp2_hit"]:
                send(f"""🔥 Quiet Alpha Update

🚀 Profit: +{round(change,1)}%
💰 Price: {price}

💡 ثبت أرباحك
""")
                trade["tp2_hit"] = True

            # 🎉 TP3
            elif change >= 100 and not trade["tp3_hit"]:
                send(f"""🎉 Quiet Alpha Winner

💰 Entry: {entry}
🚀 Price: {price}
📈 Profit: +{round(change,1)}%

🔥 صفقة قوية جداً
""")
                trade["tp3_hit"] = True

        except:
            continue

# ================= RUN =================
if __name__ == "__main__":
    send("✅ Quiet Alpha PRO bot started")

    while True:
        check_tv()
        check_uw()
        track_trades()
        time.sleep(POLL_SECONDS)

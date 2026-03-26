import os
import re
import time
import imaplib
import email
from email.header import decode_header
import requests

# =========================
# ENV
# =========================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))

# منع التكرار
seen_tv_subjects = set()
seen_uw_ids = set()


# =========================
# TELEGRAM
# =========================
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=15
        )
        print("Telegram status:", r.status_code)
        print("Telegram response:", r.text[:300])
    except Exception as e:
        print("Telegram send error:", repr(e))


# =========================
# HELPERS
# =========================
def decode_mime(value):
    if not value:
        return ""
    parts = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            parts.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(part)
    return "".join(parts)


def extract_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype in ("text/plain", "text/html") and "attachment" not in disp.lower():
                payload = part.get_payload(decode=True)
                if payload:
                    body += "\n" + payload.decode(errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(errors="ignore")
    return body


def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def normalize_side(value: str) -> str:
    if not value:
        return "N/A"
    v = str(value).upper()
    if "CALL" in v or v == "C":
        return "CALL"
    if "PUT" in v or v == "P":
        return "PUT"
    return v


def extract_strike_from_contract(contract: str):
    if not contract:
        return "N/A"

    contract = contract.upper()

    # مثال OCC: SPXW260326P06550000
    m = re.search(r"[CP](\d{8})$", contract)
    if m:
        raw = m.group(1)
        try:
            strike_val = int(raw) / 1000
            if strike_val.is_integer():
                return str(int(strike_val))
            return str(strike_val)
        except Exception:
            pass

    # fallback
    m2 = re.search(r"(\d{4,5})(?:\D*)$", contract)
    if m2:
        return m2.group(1)

    return "N/A"


def extract_type_from_contract(contract: str):
    if not contract:
        return "N/A"

    contract = contract.upper()

    m = re.search(r"([CP])\d{8}$", contract)
    if m:
        return "CALL" if m.group(1) == "C" else "PUT"

    if "CALL" in contract:
        return "CALL"
    if "PUT" in contract:
        return "PUT"

    return "N/A"


def extract_symbol_from_contract(contract: str):
    if not contract:
        return "N/A"

    upper = contract.upper()

    if upper.startswith("SPXW"):
        return "SPXW"
    if upper.startswith("SPX"):
        return "SPX"
    if upper.startswith("QQQ"):
        return "QQQ"
    if upper.startswith("NDX"):
        return "NDX"

    m = re.match(r"([A-Z]+)", upper)
    return m.group(1) if m else "N/A"


# =========================
# TV EMAIL
# =========================
def parse_tv_email(subject: str, body: str):
    text = strip_html(body)

    signal_match = re.search(r"SIGNAL:\s*(CALL|PUT)", text, re.I)
    ticker_match = re.search(r"TICKER:\s*([A-Z0-9_]+)", text, re.I)
    price_match = re.search(r"PRICE:\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)

    side = signal_match.group(1).upper() if signal_match else None
    ticker = ticker_match.group(1).upper() if ticker_match else "SPX500"
    price = price_match.group(1) if price_match else "N/A"

    if not side:
        return None

    return side, ticker, price


def check_email():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        status, data = mail.search(None, "ALL")
        print("Email search:", status)

        if status != "OK":
            mail.logout()
            return

        mail_ids = data[0].split()
        latest_ids = mail_ids[-10:]

        for mail_id in reversed(latest_ids):
            status, msg_data = mail.fetch(mail_id, "(RFC822)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = decode_mime(msg.get("Subject"))
            from_addr = decode_mime(msg.get("From"))
            body = extract_email_body(msg)

            if "tradingview" not in from_addr.lower():
                continue

            if "QA_" not in subject and "SIGNAL:" not in body:
                continue

            if subject in seen_tv_subjects:
                continue

            parsed = parse_tv_email(subject, body)
            if not parsed:
                continue

            side, ticker, price = parsed

            text = f"""🔥 Quiet Alpha Signal

📊 {ticker} {side}
💰 Entry: {price}

✉️ Source: TradingView Email"""

            send_telegram(text)
            seen_tv_subjects.add(subject)
            print("TV sent:", subject)
            break

        mail.logout()

    except Exception as e:
        print("Email Error:", repr(e))


# =========================
# UW API
# =========================
def check_uw():
    try:
        url = "https://api.unusualwhales.com/api/alerts"
        headers = {
            "Authorization": f"Bearer {UW_API_KEY}",
            "Accept": "application/json"
        }

        r = requests.get(url, headers=headers, timeout=15)

        print("UW STATUS:", r.status_code)
        print("UW RAW TEXT:", r.text[:500])

        if r.status_code != 200:
            return

        data = r.json()
        alerts = data.get("data", data) if isinstance(data, dict) else data

        if not alerts:
            print("No UW alerts")
            return

        for alert in alerts[:10]:
            alert_id = str(
                alert.get("id")
                or alert.get("_id")
                or alert.get("uuid")
                or alert.get("option_id")
                or f"{alert.get('contract')}_{alert.get('created_at')}_{alert.get('premium')}"
            )

            if alert_id in seen_uw_ids:
                continue

            option = alert.get("option", {}) or {}

            contract = (
                alert.get("contract")
                or alert.get("option_symbol")
                or option.get("contract")
                or option.get("symbol")
                or alert.get("symbol")
                or ""
            )

            symbol = (
                alert.get("symbol")
                or alert.get("ticker")
                or alert.get("underlying")
                or option.get("symbol")
                or option.get("ticker")
                or extract_symbol_from_contract(contract)
                or "N/A"
            )

            strike = (
                alert.get("strike")
                or option.get("strike")
                or option.get("strike_price")
                or extract_strike_from_contract(contract)
                or "N/A"
            )

            option_type = normalize_side(
                alert.get("type")
                or alert.get("side")
                or option.get("type")
                or option.get("side")
                or extract_type_from_contract(contract)
            )

            premium = (
                alert.get("premium")
                or alert.get("value")
                or alert.get("total_premium")
                or alert.get("notional")
                or alert.get("transaction_value")
            )

            # fallback حسابي
            if not premium:
                price = alert.get("price")
                size = alert.get("size") or alert.get("volume")

                if price and size:
                    try:
                        premium = round(float(price) * float(size), 2)
                    except Exception:
                        premium = "N/A"

            if not premium:
                premium = "N/A"

            msg = f"""🐋 UW FLOW

📊 {symbol}
🎯 Strike: {strike}
📌 Type: {option_type}
💰 Premium: {premium}
🧾 Contract: {contract or 'N/A'}

⚡ Quiet Alpha Flow Insight
"""

            send_telegram(msg)
            seen_uw_ids.add(alert_id)
            print("UW sent:", alert_id)
            break

    except Exception as e:
        print("UW Error:", repr(e))


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    send_telegram("✅ Quiet Alpha bot started")

    while True:
        check_email()
        check_uw()
        time.sleep(POLL_SECONDS)

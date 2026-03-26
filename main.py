import os
import re
import time
import imaplib
import email
from email.header import decode_header
import requests

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGNAL_CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

seen_subjects = set()


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={"chat_id": SIGNAL_CHAT_ID, "text": text},
        timeout=15
    )
    print("Telegram:", r.status_code, r.text[:200])


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
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")

    status, data = mail.search(None, "ALL")
    print("Search:", status)

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

        if subject in seen_subjects:
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
        seen_subjects.add(subject)
        print("Sent:", subject)
        break

    mail.logout()


if __name__ == "__main__":
    send_telegram("✅ Quiet Alpha TV-only bot started")
    while True:
        try:
            check_email()
        except Exception as e:
            print("Main loop error:", e)
            send_telegram(f"⚠️ TV-only bot error: {e}")

        time.sleep(20)

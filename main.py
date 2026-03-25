import os
import time
import imaplib
import email
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not CHAT_ID:
    raise ValueError("SIGNAL_CHAT_ID is missing")

if not EMAIL_USER:
    raise ValueError("EMAIL_USER is missing")

if not EMAIL_PASS:
    raise ValueError("EMAIL_PASS is missing")


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text,
    }
    r = requests.post(url, data=data, timeout=20)
    print("Telegram:", r.status_code, r.text)


def extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition.lower():
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(errors="ignore")
    return ""


def parse_signal(subject: str, body: str) -> dict | None:
    text = f"{subject}\n{body}"

    if "SIGNAL: CALL" in text:
        signal = "CALL"
    elif "SIGNAL: PUT" in text:
        signal = "PUT"
    else:
        return None

    ticker = "SPX500"
    price = "N/A"
    signal_time = "N/A"

    if "TICKER:" in text:
        try:
            ticker = text.split("TICKER:")[1].splitlines()[0].strip()
        except Exception:
            pass

    if "PRICE:" in text:
        try:
            price = text.split("PRICE:")[1].splitlines()[0].strip()
        except Exception:
            pass

    if "TIME:" in text:
        try:
            signal_time = text.split("TIME:")[1].splitlines()[0].strip()
        except Exception:
            pass

    return {
        "signal": signal,
        "ticker": ticker,
        "price": price,
        "time": signal_time,
        "subject": subject,
    }


def format_message(parsed: dict) -> str:
    return (
        "🔥 Quiet Alpha Signal\n\n"
        f"📊 {parsed['ticker']} {parsed['signal']}\n"
        f"💰 Entry: {parsed['price']}\n"
        f"⏰ Time: {parsed['time']}\n\n"
        "📩 Source: TradingView Email"
    )


def check_email() -> None:
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")

    status, messages = mail.search(None, "(UNSEEN)")
    if status != "OK":
        print("Email search failed")
        mail.logout()
        return

    mail_ids = messages[0].split()

    for mail_id in mail_ids:
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = msg.get("Subject", "") or ""
        body = extract_body(msg)

        parsed = parse_signal(subject, body)
        if not parsed:
            continue

        text = format_message(parsed)
        send_telegram(text)

    mail.logout()


def main() -> None:
    print("Quiet Alpha email bot started ✅")
    while True:
        try:
            check_email()
        except Exception as e:
            print("Error:", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

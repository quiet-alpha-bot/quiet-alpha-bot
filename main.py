import os
import re
import time
import json
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta, timezone

import requests


# =========================
# CONFIG
# =========================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = "imap.gmail.com"

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGNAL_CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

UW_API_KEY = os.getenv("UW_API_KEY")

# فلاتر أولية
MIN_PREMIUM = 250000
MIN_VOL_OI = 3.0
MIN_VOLUME = 500
MAX_ENTRY_PRICE = 8.0
MATCH_WINDOW_MINUTES = 10
DEDUP_WINDOW_MINUTES = 15

# نحتفظ بآخر TV signal
latest_tv_signal = None

# نحتفظ بالمفاتيح المرسلة لمنع التكرار
sent_signals = {}

# رابط API الأساسي - عدليه إذا كنتِ تستخدمين endpoint مختلف في نسختك الحالية
UW_BASE_URL = "https://api.unusualwhales.com/api"


# =========================
# HELPERS
# =========================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": SIGNAL_CHAT_ID,
        "text": text
    }
    try:
        requests.post(url, data=payload, timeout=15)
    except Exception as e:
        print("Telegram send error:", e)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def cleanup_sent_signals():
    cutoff = now_utc() - timedelta(minutes=DEDUP_WINDOW_MINUTES)
    to_delete = [k for k, v in sent_signals.items() if v < cutoff]
    for k in to_delete:
        del sent_signals[k]


def was_sent_recently(trade_key: str) -> bool:
    cleanup_sent_signals()
    return trade_key in sent_signals


def mark_sent(trade_key: str):
    sent_signals[trade_key] = now_utc()


def decode_mime(value):
    if not value:
        return ""
    decoded = decode_header(value)
    parts = []
    for part, enc in decoded:
        if isinstance(part, bytes):
            parts.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(part)
    return "".join(parts)


def extract_email_body(msg) -> str:
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype in ("text/plain", "text/html") and "attachment" not in disp.lower():
                payload = part.get_payload(decode=True)
                if payload:
                    decoded = payload.decode(errors="ignore")
                    body += "\n" + decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(errors="ignore")

    return body


def strip_html(html: str) -> str:
    # تحويل بسيط للنص من html
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def parse_tv_email(subject: str, body: str):
    """
    نتوقع رسالة مثل:
    SIGNAL: PUT
    TICKER: SPX500
    TIME: 2026-03-25T02:36:00Z
    PRICE: 6595.10
    STRATEGY: Smart
    """

    text = strip_html(body)

    signal_match = re.search(r"SIGNAL:\s*(CALL|PUT)", text, re.I)
    ticker_match = re.search(r"TICKER:\s*([A-Z0-9_]+)", text, re.I)
    price_match = re.search(r"PRICE:\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    time_match = re.search(r"TIME:\s*([0-9T:\-\.Z]+)", text, re.I)

    side = signal_match.group(1).upper() if signal_match else None
    ticker = ticker_match.group(1).upper() if ticker_match else None
    price = safe_float(price_match.group(1), 0.0) if price_match else 0.0

    signal_time = now_utc()
    if time_match:
        try:
            signal_time = datetime.fromisoformat(
                time_match.group(1).replace("Z", "+00:00")
            )
        except Exception:
            pass

    if not side or not ticker:
        return None

    return {
        "side": side,
        "ticker": ticker,
        "price": price,
        "time": signal_time,
        "source": "TradingView Email",
        "subject": subject,
    }


def fetch_latest_tv_signal():
    """
    يقرأ آخر إيميل غير مقروء من TradingView
    """
    global latest_tv_signal

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        # نبحث عن رسائل من TradingView وغير مقروءة
        status, data = mail.search(None, '(UNSEEN FROM "noreply@tradingview.com")')
        if status != "OK":
            mail.logout()
            return latest_tv_signal

        mail_ids = data[0].split()
        if not mail_ids:
            mail.logout()
            return latest_tv_signal

        # نقرأ الأحدث أولًا
        for mail_id in reversed(mail_ids):
            status, msg_data = mail.fetch(mail_id, "(RFC822)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = decode_mime(msg.get("Subject"))
            body = extract_email_body(msg)

            parsed = parse_tv_email(subject, body)
            if parsed:
                latest_tv_signal = parsed
                print("Latest TV signal:", latest_tv_signal)
                break

        mail.logout()
        return latest_tv_signal

    except Exception as e:
        print("Gmail read error:", e)
        return latest_tv_signal


def uw_headers():
    return {
        "Authorization": f"Bearer {UW_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def normalize_uw_side(item: dict):
    """
    نحاول نفهم الاتجاه من type أو contract أو option_type
    """
    side = str(item.get("type") or item.get("option_type") or "").upper()
    if side in ("CALL", "PUT"):
        return side

    contract = str(item.get("contract") or item.get("option_chain") or "").upper()
    # مثال شائع يحتوي C أو P
    if "CALL" in contract:
        return "CALL"
    if "PUT" in contract:
        return "PUT"

    # fallback من ticker_contract
    if "C" in contract and "P" not in contract:
        return "CALL"
    if "P" in contract:
        return "PUT"

    return None


def normalize_uw_ticker(item: dict):
    ticker = str(item.get("ticker") or item.get("underlying") or "").upper()
    if not ticker:
        return None

    # نوحد SPX / SPXW / SPX500
    if ticker in ("SPX", "SPXW"):
        return "SPX500"
    return ticker


def parse_uw_time(item: dict):
    candidates = [
        item.get("created_at"),
        item.get("executed_at"),
        item.get("timestamp"),
        item.get("time"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return datetime.fromisoformat(str(candidate).replace("Z", "+00:00"))
        except Exception:
            continue
    return now_utc()


def fetch_latest_uw_signal():
    """
    عدلي endpoint هذا إذا كان endpoint مختلف عندك في Railway
    """
    endpoints_to_try = [
        f"{UW_BASE_URL}/option-trades/flow-alerts",
        f"{UW_BASE_URL}/flow-alerts",
        f"{UW_BASE_URL}/option-trade/flow-alerts",
    ]

    for url in endpoints_to_try:
        try:
            r = requests.get(url, headers=uw_headers(), timeout=20)
            if r.status_code != 200:
                continue

            data = r.json()

            # بعض الـ endpoints ترجع list وبعضها dict فيه data/results
            if isinstance(data, dict):
                items = data.get("data") or data.get("results") or data.get("items") or []
            else:
                items = data

            if not items:
                continue

            # نفترض الأحدث أول عنصر، وإذا لا فخذي الأول المناسب
            for item in items:
                side = normalize_uw_side(item)
                ticker = normalize_uw_ticker(item)
                if not side or not ticker:
                    continue

                entry_price = safe_float(
                    item.get("price") or item.get("option_price") or item.get("fill_price"), 0.0
                )
                premium = safe_float(item.get("premium") or item.get("prem") or item.get("notional"), 0.0)
                volume = safe_float(item.get("volume"), 0.0)
                oi = safe_float(item.get("oi") or item.get("open_interest"), 0.0)
                vol_oi = safe_float(item.get("vol_oi") or item.get("volume_oi_ratio"), 0.0)

                signal = {
                    "side": side,
                    "ticker": ticker,
                    "entry_price": entry_price,
                    "premium": premium,
                    "volume": volume,
                    "oi": oi,
                    "vol_oi": vol_oi,
                    "rule": str(item.get("rule") or item.get("alert_type") or ""),
                    "contract": str(item.get("contract") or item.get("option_chain") or ""),
                    "reason": str(item.get("reason") or ""),
                    "time": parse_uw_time(item),
                    "raw": item,
                }
                print("Latest UW signal:", signal)
                return signal

        except Exception as e:
            print(f"UW fetch error from {url}: {e}")

    return None


def within_match_window(tv_time: datetime, uw_time: datetime) -> bool:
    diff = abs((tv_time - uw_time).total_seconds())
    return diff <= MATCH_WINDOW_MINUTES * 60


def grade_flow(uw_signal: dict) -> str:
    if (
        uw_signal["premium"] >= 500000
        and uw_signal["vol_oi"] >= 4.0
        and uw_signal["volume"] >= 1000
    ):
        return "A+"
    if (
        uw_signal["premium"] >= MIN_PREMIUM
        and uw_signal["vol_oi"] >= MIN_VOL_OI
        and uw_signal["volume"] >= MIN_VOLUME
    ):
        return "A"
    return "B"


def classify_signal(tv_signal: dict, uw_signal: dict):
    # 1) لازم الاتجاه متطابق
    if tv_signal["side"] != uw_signal["side"]:
        return "IGNORE", "TV direction does not match UW flow"

    # 2) لازم الوقت قريب
    if not within_match_window(tv_signal["time"], uw_signal["time"]):
        return "IGNORE", "TV signal and UW flow are too far apart in time"

    # 3) فلاتر القوة
    if uw_signal["premium"] < MIN_PREMIUM:
        return "IGNORE", "UW premium is below threshold"

    if uw_signal["vol_oi"] < MIN_VOL_OI:
        return "IGNORE", "UW Vol/OI is below threshold"

    if uw_signal["volume"] < MIN_VOLUME:
        return "IGNORE", "UW volume is below threshold"

    # 4) السعر
    if uw_signal["entry_price"] > MAX_ENTRY_PRICE:
        return "WATCH", "Contract is expensive"

    return "SIGNAL", "TV + UW aligned"


def build_trade_key(tv_signal: dict, uw_signal: dict) -> str:
    return f"{tv_signal['ticker']}|{tv_signal['side']}|{uw_signal['contract']}"


def format_currency(value: float) -> str:
    return f"${value:,.0f}"


def build_message(classification: str, tv_signal: dict, uw_signal: dict, reason: str) -> str:
    side = tv_signal["side"]
    ticker = tv_signal["ticker"]
    entry = uw_signal["entry_price"] if uw_signal["entry_price"] > 0 else tv_signal["price"]
    grade = grade_flow(uw_signal)

    if classification == "SIGNAL":
        return f"""🔥 Quiet Alpha Signal

📊 {ticker} {side}
💰 Entry: {entry:.2f}
🎯 Grade: {grade}
🧠 TV Trend: Confirmed
⚡ UW Flow: Confirmed
📈 Vol/OI: {uw_signal['vol_oi']:.2f}
💵 Premium: {format_currency(uw_signal['premium'])}
📦 Volume: {int(uw_signal['volume'])}
🧾 Rule: {uw_signal['rule'] or 'N/A'}

✅ Contract price acceptable

✉️ Source: TV + UW"""

    if classification == "WATCH":
        return f"""🟡 Quiet Alpha Watch

📊 {ticker} {side}
💰 Entry: {entry:.2f}
🎯 Grade: {grade}
🧠 TV Trend: Confirmed
⚡ UW Flow: Confirmed
📈 Vol/OI: {uw_signal['vol_oi']:.2f}
💵 Premium: {format_currency(uw_signal['premium'])}

⚠️ Contract price expensive

Prefer:
- wait for pullback
- cheaper strike
- or skip

✉️ Source: TV + UW"""

    return f"""⛔ Quiet Alpha Ignore

📊 {ticker} {side}

Reason:
{reason}

✉️ Source: TV + UW"""


def process_signals():
    tv_signal = fetch_latest_tv_signal()
    if not tv_signal:
        print("No TV signal found.")
        return

    uw_signal = fetch_latest_uw_signal()
    if not uw_signal:
        print("No UW signal found.")
        return

    trade_key = build_trade_key(tv_signal, uw_signal)
    if was_sent_recently(trade_key):
        print("Duplicate skipped:", trade_key)
        return

    classification, reason = classify_signal(tv_signal, uw_signal)

    # نرسل فقط Signal و Watch
    if classification == "IGNORE":
        print("Ignored:", reason)
        return

    message = build_message(classification, tv_signal, uw_signal, reason)
    send_telegram(message)
    mark_sent(trade_key)
    print("Sent:", classification, trade_key)


if __name__ == "__main__":
    print("Quiet Alpha TV + UW engine started...")
    while True:
        try:
            process_signals()
        except Exception as e:
            print("Main loop error:", e)

        time.sleep(20)

import os
import re
import time
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
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

# =========================
# SETTINGS
# =========================
ONLY_SPX = True
TV_SIGNAL_MAX_AGE_MINUTES = 20
DEDUP_MINUTES = 10

# إذا UW ما رجّع premium نخليه يمر
ALLOW_NA_PREMIUM_MATCH = True

# =========================
# MEMORY
# =========================
seen_tv_subjects = set()
recent_sent_contracts = {}
latest_tv_signal = None

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
def now_utc():
    return datetime.utcnow()

def cleanup_recent_sent():
    cutoff = now_utc() - timedelta(minutes=DEDUP_MINUTES)
    expired = [k for k, v in recent_sent_contracts.items() if v < cutoff]
    for k in expired:
        del recent_sent_contracts[k]

def was_recently_sent(contract: str) -> bool:
    cleanup_recent_sent()
    return bool(contract) and contract in recent_sent_contracts

def mark_sent(contract: str):
    if contract:
        recent_sent_contracts[contract] = now_utc()

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

def format_money(value):
    if value in (None, "", "N/A"):
        return "N/A"
    try:
        num = float(value)
        if num.is_integer():
            return f"{int(num):,}"
        return f"{num:,.2f}"
    except Exception:
        return str(value)

def format_price(value):
    if value in (None, "", "N/A"):
        return "N/A"
    try:
        return f"{float(value):.2f}".rstrip("0").rstrip(".") if "." in f"{float(value):.2f}" else f"{float(value):.2f}"
    except Exception:
        return str(value)

def parse_float_or_none(value):
    try:
        if value in (None, "", "N/A"):
            return None
        return float(value)
    except Exception:
        return None

def extract_strike_from_contract(contract: str):
    if not contract:
        return "N/A"

    contract = contract.upper()

    # مثال OCC: SPXW260324P06590000
    m = re.search(r"[CP](\d{8})$", contract)
    if m:
        raw = m.group(1)
        try:
            strike_val = int(raw) / 1000
            return str(int(strike_val)) if strike_val.is_integer() else str(strike_val)
        except Exception:
            pass

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

def extract_expiry_from_contract(contract: str):
    if not contract:
        return "N/A"

    m = re.search(r"(\d{6})[CP]\d{8}$", contract.upper())
    if not m:
        return "N/A"

    raw = m.group(1)  # YYMMDD
    yy = int(raw[:2]) + 2000
    mm = raw[2:4]
    dd = raw[4:6]
    return f"{yy}-{mm}-{dd}"

def is_spx_symbol(symbol: str, contract: str) -> bool:
    return "SPX" in f"{symbol} {contract}".upper()

def tv_signal_is_fresh(tv_signal: dict) -> bool:
    if not tv_signal:
        return False
    return (now_utc() - tv_signal["time"]) <= timedelta(minutes=TV_SIGNAL_MAX_AGE_MINUTES)

def compute_targets(entry):
    try:
        e = float(entry)
        return {
            "tp1": round(e * 1.30, 2),
            "tp2": round(e * 1.50, 2),
            "tp3": round(e * 2.00, 2),
            "sl": round(e * 0.65, 2),
        }
    except Exception:
        return {"tp1": "N/A", "tp2": "N/A", "tp3": "N/A", "sl": "N/A"}

def derive_confidence_and_score(premium, vol_oi, sweep, rule):
    score = 60

    p = parse_float_or_none(premium)
    v = parse_float_or_none(vol_oi)

    if p is not None:
        if p >= 500000:
            score += 15
        elif p >= 250000:
            score += 10
        elif p >= 100000:
            score += 7

    if v is not None:
        if v >= 5:
            score += 10
        elif v >= 2:
            score += 6

    if str(sweep).upper() in ["YES", "TRUE", "1"]:
        score += 5

    if "REPEATED" in str(rule).upper():
        score += 5

    score = max(1, min(score, 99))

    if score >= 85:
        confidence = "HIGH"
        grade = "A+ STRONG"
    elif score >= 72:
        confidence = "MEDIUM-HIGH"
        grade = "A STRONG"
    elif score >= 60:
        confidence = "MEDIUM"
        grade = "B"
    else:
        confidence = "LOW"
        grade = "C"

    return confidence, grade, score

def build_reason(uw):
    vol_oi_text = uw["vol_oi"] if uw["vol_oi"] != "N/A" else "N/A"
    direction_text = "upside exposure" if uw["type"] == "CALL" else "downside exposure"
    return (
        f"Repeated high-volume hits (V/OI ~{vol_oi_text}) on this long-dated "
        f"SPX {uw['strike']} {uw['type'].lower()} suggest aggressive position-taking "
        f"for {direction_text} or institutional hedging."
    )

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
    global latest_tv_signal

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        status, data = mail.search(None, "ALL")
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
            latest_tv_signal = {
                "side": side,
                "ticker": ticker,
                "price": price,
                "time": now_utc(),
                "subject": subject,
            }

            seen_tv_subjects.add(subject)
            print("TV cached:", latest_tv_signal)
            break

        mail.logout()

    except Exception as e:
        print("Email Error:", repr(e))

# =========================
# UW
# =========================
def fetch_uw_alerts():
    url = "https://api.unusualwhales.com/api/alerts"
    headers = {
        "Authorization": f"Bearer {UW_API_KEY}",
        "Accept": "application/json"
    }

    r = requests.get(url, headers=headers, timeout=15)
    print("UW STATUS:", r.status_code)

    if r.status_code != 200:
        print("UW RAW TEXT:", r.text[:500])
        return []

    data = r.json()
    return data.get("data", data) if isinstance(data, dict) else data

def parse_uw_alert(alert):
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

    size = (
        alert.get("size")
        or alert.get("contracts")
        or option.get("size")
        or option.get("contracts")
        or "N/A"
    )

    volume = (
        alert.get("volume")
        or option.get("volume")
        or "N/A"
    )

    oi = (
        alert.get("oi")
        or alert.get("open_interest")
        or option.get("oi")
        or option.get("open_interest")
        or "N/A"
    )

    vol_oi = (
        alert.get("vol_oi")
        or alert.get("vol_oi_ratio")
        or "N/A"
    )

    if vol_oi == "N/A":
        vol = parse_float_or_none(volume)
        oi_num = parse_float_or_none(oi)
        if vol is not None and oi_num not in (None, 0):
            vol_oi = round(vol / oi_num, 2)

    sweep = (
        alert.get("sweep")
        or alert.get("is_sweep")
        or "NO"
    )
    sweep = "YES" if str(sweep).upper() in ["YES", "TRUE", "1"] else "NO"

    rule = (
        alert.get("rule")
        or alert.get("rule_name")
        or "RepeatedHits"
    )

    live_price = (
        alert.get("price")
        or option.get("price")
        or option.get("mark")
        or option.get("last")
        or "N/A"
    )

    if not premium:
        p = parse_float_or_none(live_price)
        s = parse_float_or_none(size)
        if p is not None and s is not None:
            premium = round(p * s * 100, 2)
        else:
            premium = "N/A"

    expiry = (
        alert.get("expiry")
        or option.get("expiry")
        or extract_expiry_from_contract(contract)
    )

    # لو UW رجعها مباشرة، نستخدمها
    confidence = alert.get("confidence")
    grade = alert.get("grade")
    score = alert.get("score")

    targets = alert.get("targets") or {}
    tp1 = targets.get("tp1")
    tp2 = targets.get("tp2")
    tp3 = targets.get("tp3")

    stop = alert.get("stop") or alert.get("stop_loss")
    reason = alert.get("reason")

    return {
        "contract": contract,
        "symbol": symbol,
        "strike": strike,
        "type": option_type,
        "premium": premium,
        "size": size,
        "volume": volume,
        "oi": oi,
        "vol_oi": vol_oi,
        "sweep": sweep,
        "rule": rule,
        "expiry": expiry,
        "live_price": live_price,
        "confidence": confidence,
        "grade": grade,
        "score": score,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stop": stop,
        "reason": reason,
    }

def check_uw():
    try:
        alerts = fetch_uw_alerts()
        if not alerts:
            print("No UW alerts")
            return

        for raw_alert in alerts[:10]:
            uw = parse_uw_alert(raw_alert)
            contract = uw["contract"]

            if not contract:
                continue

            if ONLY_SPX and not is_spx_symbol(uw["symbol"], contract):
                continue

            if was_recently_sent(contract):
                print("UW duplicate skipped:", contract)
                continue

            if not latest_tv_signal:
                print("UW skipped: no cached TV signal")
                continue

            if not tv_signal_is_fresh(latest_tv_signal):
                print("UW skipped: TV signal stale")
                continue

            if latest_tv_signal["side"] != uw["type"]:
                print("UW skipped: side mismatch", latest_tv_signal["side"], uw["type"])
                continue

            premium_num = parse_float_or_none(uw["premium"])
            if premium_num is None and not ALLOW_NA_PREMIUM_MATCH:
                print("UW skipped: premium missing")
                continue

            entry = parse_float_or_none(latest_tv_signal["price"])
            if entry is None:
                print("UW skipped: invalid TV entry")
                continue

            # إذا UW ما رجع confidence/grade/score نحسبها
            confidence = uw["confidence"]
            grade = uw["grade"]
            score = uw["score"]

            if not confidence or not grade or not score:
                confidence, grade, score = derive_confidence_and_score(
                    uw["premium"], uw["vol_oi"], uw["sweep"], uw["rule"]
                )

            # إذا UW ما رجع targets/stop نحسبها من Entry
            tp1 = uw["tp1"]
            tp2 = uw["tp2"]
            tp3 = uw["tp3"]
            stop = uw["stop"]

            if not tp1 or not tp2 or not tp3 or not stop:
                targets = compute_targets(entry)
                tp1 = tp1 or targets["tp1"]
                tp2 = tp2 or targets["tp2"]
                tp3 = tp3 or targets["tp3"]
                stop = stop or targets["sl"]

            reason = uw["reason"] or build_reason(uw)

            msg = f"""🔥 Quiet Alpha Signal

{uw['symbol']} {uw['type']}
Strike: {uw['strike']}
Expiry: {uw['expiry']}
Entry: {format_price(entry)}

Confidence: {confidence}
Grade: {grade}
Score: {score}/100

💰 Premium: ${format_money(uw['premium'])}
📦 Size: {format_money(uw['size'])}
📊 Volume: {format_money(uw['volume'])}
📌 OI: {format_money(uw['oi'])}
📈 Vol/OI: {uw['vol_oi']}
🧹 Sweep: {uw['sweep']}
🧠 Rule: {uw['rule']}

🎯 Targets:
TP1: {format_price(tp1)}
TP2: {format_price(tp2)}
TP3: {format_price(tp3)}

⚠️ Stop:
{format_price(stop)}

🪪 Contract:
{uw['contract']}

🧠 Reason:
{reason}

هذه ليست توصية شراء أو بيع."""

            send_telegram(msg)
            mark_sent(contract)
            print("Signal sent:", contract)
            break

    except Exception as e:
        print("UW Error:", repr(e))

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    send_telegram("✅ Quiet Alpha Final bot started")

    while True:
        check_email()
        check_uw()
        time.sleep(POLL_SECONDS)

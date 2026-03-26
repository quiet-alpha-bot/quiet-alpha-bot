# ===== QUIET ALPHA SMART MATCH V2 =====
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
TV_SIGNAL_MAX_AGE_MINUTES = 20
DEDUP_MINUTES = 10
ONLY_SPX = True

# =========================
# MEMORY
# =========================
seen_tv_subjects = set()
recent_sent_contracts = {}
recent_sent_strikes = {}
recent_tv_keys = {}   # key -> datetime
latest_tv_signal = None

# =========================
# TELEGRAM
# =========================
def send_telegram(msg: str):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=10,
        )
        print("Telegram:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram send error:", repr(e))

# =========================
# HELPERS
# =========================
def now_utc():
    return datetime.utcnow()

def cleanup_recent():
    cutoff = now_utc() - timedelta(minutes=DEDUP_MINUTES)

    for k in list(recent_sent_contracts):
        if recent_sent_contracts[k] < cutoff:
            del recent_sent_contracts[k]

    for k in list(recent_sent_strikes):
        if recent_sent_strikes[k] < cutoff:
            del recent_sent_strikes[k]

    for k in list(recent_tv_keys):
        if recent_tv_keys[k] < cutoff:
            del recent_tv_keys[k]

def was_recent_contract(contract: str) -> bool:
    cleanup_recent()
    return bool(contract) and contract in recent_sent_contracts

def mark_contract(contract: str):
    if contract:
        recent_sent_contracts[contract] = now_utc()

def strike_key(symbol: str, strike: str, side: str) -> str:
    return f"{symbol}_{strike}_{side}".upper()

def was_recent_strike(symbol: str, strike: str, side: str) -> bool:
    cleanup_recent()
    return strike_key(symbol, strike, side) in recent_sent_strikes

def mark_strike(symbol: str, strike: str, side: str):
    recent_sent_strikes[strike_key(symbol, strike, side)] = now_utc()

def tv_key(ticker: str, side: str, price: str) -> str:
    return f"{ticker}_{side}_{price}"

def was_recent_tv_signal(ticker: str, side: str, price: str) -> bool:
    cleanup_recent()
    return tv_key(ticker, side, price) in recent_tv_keys

def mark_tv_signal(ticker: str, side: str, price: str):
    recent_tv_keys[tv_key(ticker, side, price)] = now_utc()

def decode_mime(value):
    if not value:
        return ""
    out = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(part)
    return "".join(out)

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

def parse_float_or_none(value):
    try:
        if value in (None, "", "N/A"):
            return None
        return float(value)
    except Exception:
        return None

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
        num = float(value)
        s = f"{num:.2f}"
        return s.rstrip("0").rstrip(".")
    except Exception:
        return str(value)

def extract_symbol_from_contract(contract: str):
    if not contract:
        return "N/A"
    u = contract.upper()
    if u.startswith("SPXW"):
        return "SPXW"
    if u.startswith("SPX"):
        return "SPX"
    m = re.match(r"([A-Z]+)", u)
    return m.group(1) if m else "N/A"

def extract_strike_from_contract(contract: str):
    if not contract:
        return "N/A"

    u = contract.upper()
    m = re.search(r"[CP](\d{8})$", u)
    if m:
        raw = m.group(1)
        try:
            val = int(raw) / 1000
            return str(int(val)) if val.is_integer() else str(val)
        except Exception:
            pass

    m2 = re.search(r"(\d{4,5})(?:\D*)$", u)
    return m2.group(1) if m2 else "N/A"

def extract_type_from_contract(contract: str):
    if not contract:
        return "N/A"
    u = contract.upper()
    m = re.search(r"([CP])\d{8}$", u)
    if m:
        return "CALL" if m.group(1) == "C" else "PUT"
    return "CALL" if "CALL" in u else "PUT" if "PUT" in u else "N/A"

def extract_expiry_from_contract(contract: str):
    if not contract:
        return "N/A"
    m = re.search(r"(\d{6})[CP]\d{8}$", contract.upper())
    if not m:
        return "N/A"
    raw = m.group(1)
    yy = int(raw[:2]) + 2000
    mm = raw[2:4]
    dd = raw[4:6]
    return f"{yy}-{mm}-{dd}"

def is_spx_symbol(symbol: str, contract: str) -> bool:
    return "SPX" in f"{symbol} {contract}".upper()

def tv_signal_is_fresh(signal: dict) -> bool:
    if not signal:
        return False
    return (now_utc() - signal["time"]) <= timedelta(minutes=TV_SIGNAL_MAX_AGE_MINUTES)

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
        return "HIGH", "A+ STRONG", score
    if score >= 72:
        return "MEDIUM-HIGH", "A STRONG", score
    if score >= 60:
        return "MEDIUM", "B", score
    return "LOW", "C", score

def build_reason(uw):
    ratio = uw["vol_oi"] if uw["vol_oi"] != "N/A" else "N/A"
    direction = "upside exposure" if uw["type"] == "CALL" else "downside exposure"
    return (
        f"Repeated high-volume hits (V/OI ~{ratio}) on this long-dated "
        f"SPX {uw['strike']} {uw['type'].lower()} suggest aggressive position-taking "
        f"for {direction} or institutional hedging."
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

def check_tv():
    global latest_tv_signal

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        status, data = mail.search(None, "ALL")
        if status != "OK":
            mail.logout()
            return

        ids = data[0].split()[-10:]

        for mail_id in reversed(ids):
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

            parsed = parse_tv_email(subject, body)
            if not parsed:
                continue

            side, ticker, price = parsed

            # لا تكرر نفس TV alert قريبًا
            if was_recent_tv_signal(ticker, side, price):
                continue

            latest_tv_signal = {
                "side": side,
                "ticker": ticker,
                "price": price,
                "time": now_utc(),
                "subject": subject,
            }

            send_telegram(f"""📡 Quiet Alpha TV Alert

{ticker} {side}
Chart Price: {format_price(price)}

UW confirmation pending...""")

            mark_tv_signal(ticker, side, price)
            seen_tv_subjects.add(subject)
            print("TV cached + sent:", latest_tv_signal)
            break

        mail.logout()

    except Exception as e:
        print("TV Error:", repr(e))

# =========================
# UW
# =========================
def fetch_uw_alerts():
    try:
        r = requests.get(
            "https://api.unusualwhales.com/api/alerts",
            headers={
                "Authorization": f"Bearer {UW_API_KEY}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        print("UW STATUS:", r.status_code)
        if r.status_code != 200:
            print("UW RAW TEXT:", r.text[:500])
            return []
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data
    except Exception as e:
        print("UW fetch error:", repr(e))
        return []

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
        or "N/A"
    )

    size = (
        alert.get("size")
        or alert.get("contracts")
        or option.get("size")
        or option.get("contracts")
        or "N/A"
    )

    volume = alert.get("volume") or option.get("volume") or "N/A"
    oi = (
        alert.get("oi")
        or alert.get("open_interest")
        or option.get("oi")
        or option.get("open_interest")
        or "N/A"
    )

    vol_oi = alert.get("vol_oi") or alert.get("vol_oi_ratio") or "N/A"
    if vol_oi == "N/A":
        vol = parse_float_or_none(volume)
        oi_num = parse_float_or_none(oi)
        if vol is not None and oi_num not in (None, 0):
            vol_oi = round(vol / oi_num, 2)

    sweep = alert.get("sweep") or alert.get("is_sweep") or "NO"
    sweep = "YES" if str(sweep).upper() in ["YES", "TRUE", "1"] else "NO"

    rule = alert.get("rule") or alert.get("rule_name") or "RepeatedHits"

    live_price = (
        alert.get("price")
        or option.get("price")
        or option.get("mark")
        or option.get("last")
        or "N/A"
    )

    # رفض سعر المؤشر إذا دخل بالغلط
    lp = parse_float_or_none(live_price)
    if lp is not None and lp > 1000:
        live_price = "N/A"

    expiry = (
        alert.get("expiry")
        or option.get("expiry")
        or extract_expiry_from_contract(contract)
    )

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
        "strike": str(strike),
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
    global latest_tv_signal

    alerts = fetch_uw_alerts()
    if not alerts:
        return

    for raw in alerts[:10]:
        uw = parse_uw_alert(raw)
        contract = uw["contract"]

        if not contract:
            continue

        if ONLY_SPX and not is_spx_symbol(uw["symbol"], contract):
            continue

        if was_recent_contract(contract):
            continue

        if was_recent_strike(uw["symbol"], uw["strike"], uw["type"]):
            continue

        if not latest_tv_signal or not tv_signal_is_fresh(latest_tv_signal):
            continue

        # التطابق الذكي: نفس الاتجاه فقط
        if latest_tv_signal["side"] != uw["type"]:
            continue

        # entry = سعر العقد فقط
        entry = parse_float_or_none(uw["live_price"])
        if entry is None:
            print("UW skipped: no valid option price")
            continue

        confidence = uw["confidence"]
        grade = uw["grade"]
        score = uw["score"]

        if not confidence or not grade or not score:
            confidence, grade, score = derive_confidence_and_score(
                uw["premium"], uw["vol_oi"], uw["sweep"], uw["rule"]
            )

        tp1, tp2, tp3, stop = uw["tp1"], uw["tp2"], uw["tp3"], uw["stop"]
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
        mark_contract(contract)
        mark_strike(uw["symbol"], uw["strike"], uw["type"])
        print("Signal sent:", contract)
        break

# =========================
# RUN
# =========================
if __name__ == "__main__":
    send_telegram("✅ Quiet Alpha Smart Match V2 bot started")

    while True:
        check_tv()
        check_uw()
        time.sleep(POLL_SECONDS)

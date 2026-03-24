import os
import time
import requests
from datetime import datetime, timezone

from openai import OpenAI

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not CHAT_ID:
    raise ValueError("SIGNAL_CHAT_ID is missing")

if not UW_API_KEY:
    raise ValueError("UW_API_KEY is missing")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
UW_FLOW_ALERTS_URL = "https://api.unusualwhales.com/api/option-trades/flow-alerts"

POLL_SECONDS = 20
SEND_WINDOW_SECONDS = 300  # 5 minutes

# Quiet Alpha Elite filter
TARGET_TICKER = "SPXW"
MIN_PREMIUM = 500_000
MIN_SIZE = 400
MIN_VOLUME = 2000
MIN_OPEN_INTEREST = 500
MIN_VOL_OI_RATIO = 2.0
MIN_PRICE = 0.5
MAX_PRICE = 20.0
MIN_DTE = 0
MAX_DTE = 1
LIMIT = 100

last_sent_time = 0
sent_contracts = {}


def telegram_send(text: str) -> None:
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
    }
    r = requests.post(TELEGRAM_URL, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")


def parse_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def parse_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return int(default)
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def compute_dte(expiry_str: str) -> int | None:
    if not expiry_str:
        return None
    try:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        now_utc = datetime.now(timezone.utc).date()
        return (expiry - now_utc).days
    except ValueError:
        return None


def build_trade_key(trade: dict) -> str:
    option_chain = trade.get("option_chain", "")
    ticker = trade.get("ticker", "")
    expiry = trade.get("expiry", "")
    strike = str(trade.get("strike", ""))
    opt_type = str(trade.get("type", ""))
    return f"{ticker}|{expiry}|{strike}|{opt_type}|{option_chain}"


def ai_reason_summary(trade: dict) -> str:
    if not client:
        return "High-premium SPXW flow with strong volume/OI and institutional-quality characteristics."

    prompt = f"""
Write one short professional reason for this options flow signal.
Keep it to one line only.

Ticker: {trade.get("ticker")}
Type: {trade.get("type")}
Strike: {trade.get("strike")}
Expiry: {trade.get("expiry")}
Option chain: {trade.get("option_chain")}
Premium: {trade.get("total_premium")}
Size: {trade.get("total_size")}
Volume: {trade.get("volume")}
Open Interest: {trade.get("open_interest")}
Volume/OI Ratio: {trade.get("volume_oi_ratio")}
Price: {trade.get("price")}
Sweep: {trade.get("has_sweep")}
Opening: {trade.get("all_opening_trades")}
Rule: {trade.get("alert_rule")}
"""

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
        )
        text = (response.output_text or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"OpenAI fallback: {e}")

    return "High-premium SPXW flow with strong volume/OI and institutional-quality characteristics."


def grade_signal(trade: dict) -> tuple[str, str, int]:
    score = 0

    premium = parse_float(trade.get("total_premium"))
    size = parse_int(trade.get("total_size"))
    volume = parse_int(trade.get("volume"))
    oi = parse_int(trade.get("open_interest"))
    vol_oi = parse_float(trade.get("volume_oi_ratio"))
    price = parse_float(trade.get("price"))
    has_sweep = bool(trade.get("has_sweep"))
    opening = bool(trade.get("all_opening_trades"))

    if premium >= 1_000_000:
        score += 32
    elif premium >= 750_000:
        score += 28
    elif premium >= 500_000:
        score += 22

    if size >= 1500:
        score += 18
    elif size >= 1000:
        score += 15
    elif size >= 400:
        score += 10

    if volume >= 10000:
        score += 14
    elif volume >= 5000:
        score += 11
    elif volume >= 2000:
        score += 8

    if oi >= 5000:
        score += 10
    elif oi >= 2000:
        score += 7
    elif oi >= 500:
        score += 4

    if vol_oi >= 4:
        score += 14
    elif vol_oi >= 3:
        score += 11
    elif vol_oi >= 2:
        score += 7

    if MIN_PRICE <= price <= MAX_PRICE:
        score += 6

    if has_sweep:
        score += 4

    if opening:
        score += 4

    if score >= 78:
        return "A+ ELITE", "HIGH", score
    if score >= 62:
        return "A STRONG", "MEDIUM-HIGH", score
    if score >= 48:
        return "B WATCH", "MEDIUM", score
    return "REJECT", "LOW", score


def passes_filter(trade: dict) -> bool:
    ticker = str(trade.get("ticker", "")).upper()
    premium = parse_float(trade.get("total_premium"))
    size = parse_int(trade.get("total_size"))
    volume = parse_int(trade.get("volume"))
    oi = parse_int(trade.get("open_interest"))
    vol_oi = parse_float(trade.get("volume_oi_ratio"))
    price = parse_float(trade.get("price"))
    dte = compute_dte(trade.get("expiry", ""))

    if ticker != TARGET_TICKER:
        return False
    if premium < MIN_PREMIUM:
        return False
    if size < MIN_SIZE:
        return False
    if volume < MIN_VOLUME:
        return False
    if oi < MIN_OPEN_INTEREST:
        return False
    if vol_oi < MIN_VOL_OI_RATIO:
        return False
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return False
    if dte is None or not (MIN_DTE <= dte <= MAX_DTE):
        return False

    grade, _, _ = grade_signal(trade)
    if grade == "REJECT":
        return False

    return True


def build_targets(entry_price: float) -> tuple[float, float, float]:
    tp1 = round(entry_price * 1.30, 2)
    tp2 = round(entry_price * 1.50, 2)
    tp3 = round(entry_price * 2.00, 2)
    return tp1, tp2, tp3


def build_stop(entry_price: float) -> float:
    if 0.5 <= entry_price <= 2.0:
        return round(entry_price * 0.75, 2)
    if 2.01 <= entry_price <= 5.0:
        return round(entry_price * 0.70, 2)
    return round(entry_price * 0.65, 2)


def format_signal(trade: dict) -> str:
    ticker = trade.get("ticker", "N/A")
    option_type = str(trade.get("type", "")).upper()
    strike = trade.get("strike", "N/A")
    expiry = trade.get("expiry", "N/A")
    option_chain = trade.get("option_chain", "N/A")
    price = parse_float(trade.get("price"))
    premium = parse_float(trade.get("total_premium"))
    size = parse_int(trade.get("total_size"))
    volume = parse_int(trade.get("volume"))
    oi = parse_int(trade.get("open_interest"))
    vol_oi = parse_float(trade.get("volume_oi_ratio"))
    alert_rule = trade.get("alert_rule", "N/A")
    has_sweep = "YES" if trade.get("has_sweep") else "NO"

    grade, confidence, score = grade_signal(trade)
    tp1, tp2, tp3 = build_targets(price)
    stop = build_stop(price)
    reason = ai_reason_summary(trade)

    msg = f"""🔥 Quiet Alpha Signal

{ticker} {option_type}
Strike: {strike}
Expiry: {expiry}
Entry: {price:.2f}

Confidence: {confidence}
Grade: {grade}
Score: {score}/100

💰 Premium: ${premium:,.0f}
📦 Size: {size}
📊 Volume: {volume}
📌 OI: {oi}
📈 Vol/OI: {vol_oi:.2f}
🧹 Sweep: {has_sweep}
🧠 Rule: {alert_rule}

🎯 Targets:
TP1: {tp1}
TP2: {tp2}
TP3: {tp3}

⚠️ Stop:
{stop}

🪪 Contract:
{option_chain}

🧠 Reason:
{reason}

هذه ليست توصية شراء أو بيع
"""
    return msg


def fetch_flow_alerts() -> list[dict]:
    headers = {
        "Authorization": f"Bearer {UW_API_KEY}",
        "Accept": "application/json",
    }

    params = {
        "ticker_symbol": TARGET_TICKER,
        "min_premium": MIN_PREMIUM,
        "min_size": MIN_SIZE,
        "min_volume": MIN_VOLUME,
        "min_open_interest": MIN_OPEN_INTEREST,
        "min_volume_oi_ratio": MIN_VOL_OI_RATIO,
        "min_price": MIN_PRICE,
        "max_price": MAX_PRICE,
        "min_dte": MIN_DTE,
        "max_dte": MAX_DTE,
        "limit": LIMIT,
    }

    r = requests.get(UW_FLOW_ALERTS_URL, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()

    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        if isinstance(data, list):
            return data

    if isinstance(payload, list):
        return payload

    return []


def rank_trade(trade: dict) -> float:
    premium = parse_float(trade.get("total_premium"))
    size = parse_int(trade.get("total_size"))
    volume = parse_int(trade.get("volume"))
    vol_oi = parse_float(trade.get("volume_oi_ratio"))
    score = grade_signal(trade)[2]

    # weighted ranking
    return (
        score * 1000
        + premium * 0.01
        + size * 5
        + volume * 1.5
        + vol_oi * 500
    )


def cleanup_sent_contracts() -> None:
    now_ts = time.time()
    expired_keys = [
        key for key, ts in sent_contracts.items()
        if now_ts - ts > SEND_WINDOW_SECONDS
    ]
    for key in expired_keys:
        del sent_contracts[key]


def main():
    global last_sent_time

    print("Quiet Alpha live flow monitor started.")

    while True:
        try:
            cleanup_sent_contracts()
            trades = fetch_flow_alerts()

            candidates = []
            for trade in trades:
                if not passes_filter(trade):
                    continue

                key = build_trade_key(trade)
                if key in sent_contracts:
                    continue

                candidates.append(trade)

            if candidates:
                now_ts = time.time()

                if now_ts - last_sent_time >= SEND_WINDOW_SECONDS:
                    best_trade = max(candidates, key=rank_trade)
                    key = build_trade_key(best_trade)

                    sent_contracts[key] = now_ts
                    last_sent_time = now_ts

                    msg = format_signal(best_trade)
                    telegram_send(msg)
                    print(f"Sent best signal: {best_trade.get('option_chain')}")

        except Exception as e:
            error_msg = f"Quiet Alpha bot error: {e}"
            print(error_msg)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

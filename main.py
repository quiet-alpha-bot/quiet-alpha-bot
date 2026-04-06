import os
import time
import requests
from datetime import datetime, timezone, timedelta
from threading import Thread
from flask import Flask, request, jsonify

# ==========================================
# ⚙️ الإعدادات
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")
PORT = int(os.getenv("PORT", "5000"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not CHAT_ID:
    raise ValueError("SIGNAL_CHAT_ID is missing")

if not UW_API_KEY:
    raise ValueError("UW_API_KEY is missing")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
UW_FLOW_ALERTS_URL = "https://api.unusualwhales.com/api/option-trades/flow-alerts"

POLL_SECONDS = 20
MATCH_WINDOW_MINUTES = 3
DEBUG = True

# Quiet Alpha filters
TARGET_TICKER = "SPXW"
MIN_PREMIUM = 200_000
MIN_SIZE = 150
MIN_VOLUME = 300
MIN_OPEN_INTEREST = 500
MIN_VOL_OI_RATIO = 1.0
MIN_PRICE = 0.5
MAX_PRICE = 20.0
MIN_DTE = 0
MAX_DTE = 1
LIMIT = 100

# ==========================================
# 🧠 الذاكرة
# ==========================================
seen_ids = set()
sent_matches = set()

tv_cache = {
    "CALL": None,
    "PUT": None,
}

uw_cache = {
    "CALL": {"time": None, "trade": None},
    "PUT": {"time": None, "trade": None},
}

# ==========================================
# 🌐 Flask
# ==========================================
app = Flask(__name__)

# ==========================================
# 🛠️ Helpers
# ==========================================
def now():
    return datetime.now()


def log(msg: str):
    if DEBUG:
        print(msg)


def telegram_send(text: str) -> None:
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    response = requests.post(TELEGRAM_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")


def send_msg(text: str) -> None:
    try:
        telegram_send(text)
        log("📤 Telegram sent successfully")
    except Exception as e:
        print(f"Telegram Error: {e}")


def parse_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return float(default)


def parse_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return int(default)
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def normalize_side(value) -> str:
    v = str(value or "").upper().strip()
    if v in ("CALL", "C"):
        return "CALL"
    if v in ("PUT", "P"):
        return "PUT"
    return "N/A"


def compute_dte(expiry_str: str):
    if not expiry_str:
        return None
    try:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        now_utc = datetime.now(timezone.utc).date()
        return (expiry - now_utc).days
    except ValueError:
        return None


def build_trade_key(trade: dict) -> str:
    created_at = trade.get("created_at", "")
    option_chain = (
        trade.get("option_chain")
        or trade.get("option_symbol")
        or trade.get("contract")
        or ""
    )
    price = str(trade.get("price", ""))
    premium = str(trade.get("total_premium", ""))
    size = str(trade.get("total_size", ""))
    return f"{created_at}|{option_chain}|{price}|{premium}|{size}"


def cleanup_caches():
    cutoff = now() - timedelta(minutes=MATCH_WINDOW_MINUTES)

    for side in ("CALL", "PUT"):
        if tv_cache[side] and tv_cache[side] < cutoff:
            log(f"🧹 TV cache expired for {side}")
            tv_cache[side] = None

        if uw_cache[side]["time"] and uw_cache[side]["time"] < cutoff:
            log(f"🧹 UW cache expired for {side}")
            uw_cache[side] = {"time": None, "trade": None}


# ==========================================
# 📊 Quiet Alpha Logic
# ==========================================
def grade_signal(trade: dict):
    score = 0

    premium = parse_float(trade.get("total_premium"))
    size = parse_int(trade.get("total_size"))
    volume = parse_int(trade.get("volume"))
    oi = parse_int(trade.get("open_interest"))
    vol_oi = parse_float(trade.get("volume_oi_ratio"))
    price = parse_float(trade.get("price"))
    has_sweep = bool(trade.get("has_sweep"))
    opening = bool(trade.get("all_opening_trades"))

    if premium >= 500_000:
        score += 30
    elif premium >= 300_000:
        score += 24
    elif premium >= 200_000:
        score += 18

    if size >= 1000:
        score += 18
    elif size >= 500:
        score += 14
    elif size >= 150:
        score += 9

    if volume >= 5000:
        score += 12
    elif volume >= 1000:
        score += 9
    elif volume >= 300:
        score += 6

    if oi >= 3000:
        score += 10
    elif oi >= 1000:
        score += 7
    elif oi >= 500:
        score += 4

    if vol_oi >= 3:
        score += 12
    elif vol_oi >= 1.5:
        score += 9
    elif vol_oi >= 1:
        score += 5

    if MIN_PRICE <= price <= MAX_PRICE:
        score += 8

    if has_sweep:
        score += 6

    if opening:
        score += 4

    if score >= 75:
        return "A+ ELITE", "HIGH", score
    if score >= 60:
        return "A STRONG", "MEDIUM-HIGH", score
    if score >= 45:
        return "B WATCH", "MEDIUM", score
    return "REJECT", "LOW", score


def passes_filter(trade: dict) -> tuple[bool, str]:
    ticker = str(trade.get("ticker", "")).upper()
    premium = parse_float(trade.get("total_premium"))
    size = parse_int(trade.get("total_size"))
    volume = parse_int(trade.get("volume"))
    oi = parse_int(trade.get("open_interest"))
    vol_oi = parse_float(trade.get("volume_oi_ratio"))
    price = parse_float(trade.get("price"))
    dte = compute_dte(trade.get("expiry", ""))

    if ticker != TARGET_TICKER:
        return False, f"Rejected: ticker {ticker} != {TARGET_TICKER}"
    if premium < MIN_PREMIUM:
        return False, f"Rejected: premium {premium} < {MIN_PREMIUM}"
    if size < MIN_SIZE:
        return False, f"Rejected: size {size} < {MIN_SIZE}"
    if volume < MIN_VOLUME:
        return False, f"Rejected: volume {volume} < {MIN_VOLUME}"
    if oi < MIN_OPEN_INTEREST:
        return False, f"Rejected: OI {oi} < {MIN_OPEN_INTEREST}"
    if vol_oi < MIN_VOL_OI_RATIO:
        return False, f"Rejected: Vol/OI {vol_oi} < {MIN_VOL_OI_RATIO}"
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return False, f"Rejected: price {price} خارج النطاق"
    if dte is None:
        return False, "Rejected: DTE is None"
    if not (MIN_DTE <= dte <= MAX_DTE):
        return False, f"Rejected: DTE {dte} not in [{MIN_DTE}, {MAX_DTE}]"

    grade, _, _ = grade_signal(trade)
    if grade == "REJECT":
        return False, "Rejected: grade is REJECT"

    return True, "Passed"


def build_targets(entry_price: float):
    tp1 = round(entry_price * 1.30, 2)
    tp2 = round(entry_price * 1.50, 2)
    tp3 = round(entry_price * 2.00, 2)
    return tp1, tp2, tp3


def build_stop(entry_price: float):
    if 0.5 <= entry_price <= 2.0:
        return round(entry_price * 0.75, 2)
    if 2.01 <= entry_price <= 5.0:
        return round(entry_price * 0.70, 2)
    return round(entry_price * 0.65, 2)


def format_signal(trade: dict, reason: str):
    ticker = trade.get("ticker", "N/A")
    option_type = normalize_side(trade.get("type") or trade.get("option_type"))
    strike = trade.get("strike", "N/A")
    expiry = trade.get("expiry", "N/A")
    option_chain = (
        trade.get("option_chain")
        or trade.get("option_symbol")
        or trade.get("contract")
        or "N/A"
    )
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

    msg = f"""🔥 *Quiet Alpha Signal*

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


# ==========================================
# 🐋 UW Fetch
# ==========================================
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

    try:
        response = requests.get(UW_FLOW_ALERTS_URL, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, dict):
            data = payload.get("data", [])
            return data if isinstance(data, list) else []

        if isinstance(payload, list):
            return payload

        return []

    except Exception as e:
        log(f"❌ UW fetch error: {e}")
        return []


# ==========================================
# 🔗 Matching
# ==========================================
def execute_signal(trade: dict, reason: str):
    match_id = build_trade_key(trade)
    if match_id in sent_matches:
        log(f"🔁 Duplicate match skipped: {match_id}")
        return

    sent_matches.add(match_id)
    msg = format_signal(trade, reason)
    send_msg(msg)


def handle_tv_alert(direction: str):
    cleanup_caches()
    current_time = now()
    tv_cache[direction] = current_time

    log(f"📺 TV cached => {direction} at {current_time.strftime('%H:%M:%S')}")

    cached = uw_cache[direction]
    if cached["time"] and cached["trade"]:
        age = current_time - cached["time"]
        log(f"🧮 TV checking UW cache for {direction}, age={age}")
        if age <= timedelta(minutes=MATCH_WINDOW_MINUTES):
            log(f"✅ MATCH FOUND: TV confirmed previous UW trade ({direction})")
            execute_signal(cached["trade"], "Match Confirmed (Chart + Whale)")
            tv_cache[direction] = None
            uw_cache[direction] = {"time": None, "trade": None}
        else:
            log(f"⌛ UW cache for {direction} is too old")
    else:
        log(f"ℹ️ No UW cache yet for {direction}")


def process_whale_trade(trade: dict):
    cleanup_caches()

    passed, reason = passes_filter(trade)
    option_chain = trade.get("option_chain") or trade.get("option_symbol") or trade.get("contract") or "N/A"
    opt_type = normalize_side(trade.get("type") or trade.get("option_type"))

    if opt_type == "N/A":
        log(f"⚠️ Rejected {option_chain}: invalid side")
        return

    if not passed:
        log(f"⚠️ Rejected {option_chain}: {reason}")
        return

    log(f"✅ Passed filters: {option_chain} | side={opt_type}")

    current_time = now()
    uw_cache[opt_type] = {"time": current_time, "trade": trade}

    log(f"🐋 UW cached => {opt_type} | {option_chain} at {current_time.strftime('%H:%M:%S')}")

    tv_time = tv_cache[opt_type]
    if tv_time:
        age = current_time - tv_time
        log(f"🧮 UW checking TV cache for {opt_type}, age={age}")
        if age <= timedelta(minutes=MATCH_WINDOW_MINUTES):
            log(f"✅ MATCH FOUND: UW confirmed previous TV signal ({opt_type})")
            execute_signal(trade, "Match Confirmed (Chart + Whale)")
            tv_cache[opt_type] = None
            uw_cache[opt_type] = {"time": None, "trade": None}
        else:
            log(f"⌛ TV cache for {opt_type} is too old")
    else:
        log(f"ℹ️ No TV cache yet for {opt_type}")


# ==========================================
# 🌐 Webhook
# ==========================================
@app.route("/webhook", methods=["POST"])
def tv_webhook():
    data = request.get_json(silent=True) or {}
    log(f"🟢 Webhook endpoint hit at {now().strftime('%H:%M:%S')}")
    log(f"📩 TV Alert Received: {data}")

    direction = normalize_side(data.get("direction") or data.get("signal"))
    if direction in ("CALL", "PUT"):
        handle_tv_alert(direction)
    else:
        log("⚠️ TV alert received without valid CALL/PUT direction")

    return jsonify({"status": "received"}), 200


@app.route("/")
def health_check():
    return "Quiet Alpha Engine is Running!"


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({
        "status": "ok",
        "time": now().strftime("%H:%M:%S"),
        "tv_cache": {
            "CALL": str(tv_cache["CALL"]),
            "PUT": str(tv_cache["PUT"]),
        },
        "uw_cache": {
            "CALL": str(uw_cache["CALL"]["time"]),
            "PUT": str(uw_cache["PUT"]["time"]),
        }
    }), 200


# ==========================================
# 🔄 Monitor Loop
# ==========================================
def monitor_loop():
    log("🛰️ Quiet Alpha Monitor Active...")

    while True:
        try:
            trades = fetch_flow_alerts()
            log(f"📡 UW fetched trades: {len(trades)}")

            trades = sorted(
                trades,
                key=lambda x: x.get("created_at", ""),
                reverse=False,
            )

            for trade in trades:
                key = build_trade_key(trade)

                if key in seen_ids:
                    continue

                seen_ids.add(key)
                process_whale_trade(trade)

            if len(seen_ids) > 5000:
                seen_ids.clear()
                sent_matches.clear()
                log("🧹 seen_ids and sent_matches cleared")

        except Exception as e:
            log(f"❌ Quiet Alpha bot error: {e}")

        time.sleep(POLL_SECONDS)


# ==========================================
# 🏁 Main
# ==========================================
def main():
    send_msg(
        "🚀 *Quiet Alpha Match Engine Online*\n"
        "━━━━━━━━━━━━━━\n"
        "📡 TV Webhook Ready\n"
        "🐋 UW Flow Monitor Ready\n"
        "🎯 Waiting for CALL/PUT match\n"
        "━━━━━━━━━━━━━━\n"
        "🤍 جاهزون لأول تطابق"
    )

    Thread(target=monitor_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)


if __name__ == "__main__":
    main()

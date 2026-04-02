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
MATCH_WINDOW_MINUTES = 10
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

tv_cache = {"CALL": None, "PUT": None}

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

def log(msg):
    if DEBUG:
        print(msg)

def send_msg(text):
    try:
        requests.post(
            TELEGRAM_URL,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=20,
        )
        log("📤 Telegram sent")
    except Exception as e:
        log(f"❌ Telegram error: {e}")

def parse_float(v, d=0.0):
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except:
        return d

def parse_int(v, d=0):
    try:
        return int(float(v))
    except:
        return d

def normalize_side(v):
    v = str(v or "").upper()
    if v in ("CALL", "C"):
        return "CALL"
    if v in ("PUT", "P"):
        return "PUT"
    return "N/A"

def compute_dte(exp):
    try:
        expiry = datetime.strptime(exp, "%Y-%m-%d").date()
        return (expiry - datetime.now(timezone.utc).date()).days
    except:
        return None

def build_trade_key(t):
    return f"{t.get('created_at')}_{t.get('option_chain')}_{t.get('total_premium')}"

def cleanup():
    cutoff = now() - timedelta(minutes=MATCH_WINDOW_MINUTES)
    for s in ("CALL", "PUT"):
        if tv_cache[s] and tv_cache[s] < cutoff:
            tv_cache[s] = None
        if uw_cache[s]["time"] and uw_cache[s]["time"] < cutoff:
            uw_cache[s] = {"time": None, "trade": None}

# ==========================================
# 📊 فلترة
# ==========================================
def passes_filter(t):
    if str(t.get("ticker")).upper() != TARGET_TICKER:
        return False
    if parse_float(t.get("total_premium")) < MIN_PREMIUM:
        return False
    if parse_int(t.get("total_size")) < MIN_SIZE:
        return False
    if parse_int(t.get("volume")) < MIN_VOLUME:
        return False
    if parse_int(t.get("open_interest")) < MIN_OPEN_INTEREST:
        return False
    if parse_float(t.get("volume_oi_ratio")) < MIN_VOL_OI_RATIO:
        return False
    if not (MIN_PRICE <= parse_float(t.get("price")) <= MAX_PRICE):
        return False

    dte = compute_dte(t.get("expiry"))
    if dte is None or dte > 1:
        return False

    return True

# ==========================================
# 🧠 رسالة الإشارة
# ==========================================
def build_msg(t, reason):
    price = parse_float(t.get("price"))
    tp1 = round(price * 1.3, 2)
    tp2 = round(price * 1.5, 2)
    tp3 = round(price * 2, 2)
    stop = round(price * 0.7, 2)

    return f"""🔥 *Quiet Alpha Signal*

SPXW {normalize_side(t.get("type"))}
Strike: {t.get("strike")}
Expiry: {t.get("expiry")}
Entry: {price}

🎯 TP1: {tp1}
🎯 TP2: {tp2}
🎯 TP3: {tp3}

⚠️ Stop: {stop}

🧠 {reason}
"""

# ==========================================
# 🔗 المطابقة
# ==========================================
def execute_signal(t):
    key = build_trade_key(t)
    if key in sent_matches:
        return
    sent_matches.add(key)
    send_msg(build_msg(t, "MATCH CONFIRMED 🚀"))

def handle_tv(direction):
    cleanup()
    tv_cache[direction] = now()

    if uw_cache[direction]["time"]:
        if now() - uw_cache[direction]["time"] <= timedelta(minutes=MATCH_WINDOW_MINUTES):
            execute_signal(uw_cache[direction]["trade"])
            tv_cache[direction] = None
            uw_cache[direction] = {"time": None, "trade": None}

def handle_uw(t):
    cleanup()
    side = normalize_side(t.get("type"))
    if side == "N/A":
        return

    if not passes_filter(t):
        return

    uw_cache[side] = {"time": now(), "trade": t}

    if tv_cache[side]:
        if now() - tv_cache[side] <= timedelta(minutes=MATCH_WINDOW_MINUTES):
            execute_signal(t)
            tv_cache[side] = None
            uw_cache[side] = {"time": None, "trade": None}

# ==========================================
# 🐋 UW LOOP
# ==========================================
def monitor():
    while True:
        try:
            r = requests.get(
                UW_FLOW_ALERTS_URL,
                headers={"Authorization": f"Bearer {UW_API_KEY}"},
                timeout=20,
            )
            data = r.json().get("data", [])
            for t in data:
                k = build_trade_key(t)
                if k not in seen_ids:
                    seen_ids.add(k)
                    handle_uw(t)
        except Exception as e:
            log(e)

        time.sleep(POLL_SECONDS)

# ==========================================
# 🌐 Webhook
# ==========================================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    side = normalize_side(data.get("direction"))
    if side in ("CALL", "PUT"):
        handle_tv(side)
    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Running"

# ==========================================
# 🏁 تشغيل
# ==========================================
def main():
    send_msg("🚀 Quiet Alpha Engine Started")
    Thread(target=monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

if __name__ == "__main__":
    main()

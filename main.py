import os
import time
from threading import Thread
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# =========================
# ENV
# =========================
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
UW_API_KEY = os.getenv("UW_API_KEY")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))
PORT = int(os.getenv("PORT", "5000"))

# =========================
# SETTINGS
# =========================
MATCH_WINDOW_MINUTES = 10
MIN_PREMIUM = 100000
MIN_PRICE = 2.0
MAX_PRICE = 15.0

TARGETS = [
    {"pct": 1.30, "label": "30%", "new_sl_pct": 1.00, "emoji": "✅"},
    {"pct": 1.50, "label": "50%", "new_sl_pct": 1.20, "emoji": "🔥"},
    {"pct": 1.70, "label": "70%", "new_sl_pct": 1.40, "emoji": "✨"},
    {"pct": 2.00, "label": "100%", "new_sl_pct": 1.60, "emoji": "🎉"},
]

# =========================
# GLOBAL MEMORY
# =========================
market_status = {"vix": "Neutral", "trend": "Neutral"}

tv_cache: Dict[str, Dict[str, Optional[datetime]]] = {
    "CALL": {"time": None},
    "PUT": {"time": None},
}

uw_cache: Dict[str, Dict[str, Any]] = {
    "CALL": {"time": None, "trade": None},
    "PUT": {"time": None, "trade": None},
}

active_trades: Dict[str, Dict[str, Any]] = {}
sent_trade_ids = set()
latest_trades: List[Dict[str, Any]] = []

# =========================
# HELPERS
# =========================
def now() -> datetime:
    return datetime.now()


def normalize_option_type(value: Any) -> str:
    v = str(value or "").upper().strip()
    if v in ("CALL", "C"):
        return "CALL"
    if v in ("PUT", "P"):
        return "PUT"
    return "N/A"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except Exception:
        return default


def fmt_price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return "N/A"


def fmt_money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return "N/A"


def get_duration(start_time: datetime) -> str:
    delta = now() - start_time
    total_seconds = int(delta.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    if minutes == 0:
        return f"{seconds} sec"
    return f"{minutes} min {seconds} sec"


def build_trade_id(trade: Dict[str, Any]) -> str:
    return str(
        trade.get("option_symbol")
        or trade.get("contract")
        or trade.get("id")
        or trade.get("_id")
        or trade.get("uuid")
        or f"{trade.get('strike')}_{trade.get('option_type')}_{trade.get('price')}"
    )


def send_msg(text: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram config missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        print("Telegram:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram Error:", repr(e))


def cleanup_caches():
    cutoff = now() - timedelta(minutes=MATCH_WINDOW_MINUTES)

    for side in ("CALL", "PUT"):
        if tv_cache[side]["time"] and tv_cache[side]["time"] < cutoff:
            tv_cache[side]["time"] = None

        if uw_cache[side]["time"] and uw_cache[side]["time"] < cutoff:
            uw_cache[side]["time"] = None
            uw_cache[side]["trade"] = None


# =========================
# WEBHOOK FROM TRADINGVIEW
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    if "vix" in data:
        market_status["vix"] = str(data["vix"])

    if "trend" in data:
        market_status["trend"] = str(data["trend"])

    direction = normalize_option_type(data.get("direction") or data.get("signal"))

    print(f"[{now().strftime('%H:%M:%S')}] Webhook Update: {data}")
    print("Market Status:", market_status)

    if direction in ("CALL", "PUT"):
        handle_tv_alert(direction)

    return jsonify({"status": "ok"}), 200


# =========================
# MATCHING ENGINE
# =========================
def handle_tv_alert(direction: str):
    cleanup_caches()

    current_time = now()
    tv_cache[direction]["time"] = current_time

    send_msg(
        f"📡 *Quiet Alpha TV Alert*\n\n"
        f"Direction: *{direction}*\n"
        f"Trend: `{market_status['trend']}`\n"
        f"VIX: `{market_status['vix']}`\n\n"
        f"UW confirmation pending..."
    )

    cached = uw_cache[direction]
    if cached["time"] and cached["trade"] and (current_time - cached["time"]) <= timedelta(minutes=MATCH_WINDOW_MINUTES):
        print(f"✅ Match: TV confirmed previous whale trade ({direction})")
        execute_signal(cached["trade"], "Match Confirmed (Chart + Whale)")


def process_whale_trade(trade: Dict[str, Any]):
    cleanup_caches()

    current_time = now()
    price = safe_float(trade.get("price") or trade.get("mark") or trade.get("last"))
    premium = safe_float(
        trade.get("premium")
        or trade.get("value")
        or trade.get("total_premium")
        or trade.get("notional")
        or trade.get("transaction_value")
    )

    opt_type = normalize_option_type(
        trade.get("option_type")
        or trade.get("operation_type")
        or trade.get("type")
        or trade.get("side")
    )

    if opt_type == "N/A":
        return

    if premium < MIN_PREMIUM:
        return

    if not (MIN_PRICE <= price <= MAX_PRICE):
        return

    uw_cache[opt_type] = {"time": current_time, "trade": trade}

    tv_time = tv_cache[opt_type]["time"]
    if tv_time and (current_time - tv_time) <= timedelta(minutes=MATCH_WINDOW_MINUTES):
        print(f"✅ Match: Whale trade confirmed TV alert ({opt_type})")
        execute_signal(trade, "Match Confirmed (Chart + Whale)")


# =========================
# SIGNAL EXECUTION
# =========================
def execute_signal(trade: Dict[str, Any], reason: str):
    option_symbol = str(trade.get("option_symbol") or trade.get("contract") or "N/A")
    trade_id = build_trade_id(trade)

    if trade_id in sent_trade_ids or trade_id in active_trades:
        return

    price = safe_float(trade.get("price") or trade.get("mark") or trade.get("last"))
    strike = trade.get("strike") or "N/A"
    opt_type = normalize_option_type(
        trade.get("option_type")
        or trade.get("operation_type")
        or trade.get("type")
        or trade.get("side")
    )
    premium = safe_float(
        trade.get("premium")
        or trade.get("value")
        or trade.get("total_premium")
        or trade.get("notional")
        or trade.get("transaction_value")
    )

    if price <= 0 or opt_type == "N/A":
        return

    start_dt = now()
    active_trades[trade_id] = {
        "trade_id": trade_id,
        "option_symbol": option_symbol,
        "strike": strike,
        "side": opt_type,
        "entry": price,
        "premium": premium,
        "start": start_dt,
        "last_tp": -1,
        "closed": False,
        "highest": price,
        "stop": round(price * 0.70, 2),
        "wave_sent": False,
        "final_sent": False,
    }

    msg = (
        f"🔥 *Quiet Alpha Signal*\n\n"
        f"🎯 *{option_symbol}*\n"
        f"🧱 *Strike:* `{strike}`\n"
        f"🔥 *Type:* `{opt_type}`\n"
        f"💵 *Entry:* `${price:.2f}`\n"
        f"💰 *Premium:* `${premium:,.0f}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"📊 *Signal Grade:* `A+`\n"
        f"🧠 *Status:* `{reason}`\n"
        f"📈 *Market Context:* `{market_status['trend']} | VIX: {market_status['vix']}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 *Targets*\n"
        f"TP1: `${price * 1.30:.2f}` (+30%)\n"
        f"TP2: `${price * 1.50:.2f}` (+50%)\n"
        f"TP3: `${price * 1.70:.2f}` (+70%)\n"
        f"TP4: `${price * 2.00:.2f}` (+100%)\n\n"
        f"🛡️ *Initial Stop:* `${price * 0.70:.2f}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"⏱️ *Match Time:* `{start_dt.strftime('%H:%M:%S')}`\n\n"
        f"⚠️ هذه ليست توصية شراء أو بيع"
    )
    send_msg(msg)

    sent_trade_ids.add(trade_id)

    tv_cache[opt_type]["time"] = None
    uw_cache[opt_type]["time"] = None
    uw_cache[opt_type]["trade"] = None


# =========================
# TARGET TRACKING
# =========================
def check_targets(trade_id: str, current_price: float):
    trade = active_trades.get(trade_id)
    if not trade or trade["closed"]:
        return

    entry = trade["entry"]

    if current_price > trade["highest"]:
        trade["highest"] = current_price

    for i, target in enumerate(TARGETS):
        if current_price >= entry * target["pct"] and i > trade["last_tp"]:
            trade["last_tp"] = i
            trade["stop"] = round(entry * target["new_sl_pct"], 2)
            duration = get_duration(trade["start"])

            stop_note = {
                0: "تم رفع الوقف إلى الدخول",
                1: "تم رفع الوقف إلى +20% ربح",
                2: "تم رفع الوقف إلى +40% ربح",
                3: "تم رفع الوقف إلى +60% ربح",
            }.get(i, "تم تحديث الوقف")

            update_msg = (
                f"{target['emoji']} *Quiet Alpha Update*\n\n"
                f"🎯 *{trade['option_symbol']}*\n"
                f"📈 *Progress:* `{target['label']}`\n"
                f"💵 *Entry:* `${entry:.2f}`\n"
                f"💰 *Current:* `${current_price:.2f}`\n"
                f"⏱️ *Time Elapsed:* `{duration}`\n"
                f"━━━━━━━━━━━━━━\n"
                f"🛡️ *Updated Stop:* `${trade['stop']:.2f}`\n"
                f"💡 *Action:* {stop_note}\n"
                f"━━━━━━━━━━━━━━\n"
                f"🧠 *Note:* تم تأمين جزء من الربح بنجاح."
            )
            send_msg(update_msg)

    if trade["last_tp"] >= 3 and current_price >= entry * 2.20 and not trade.get("wave_sent", False):
        send_msg(
            f"🌊 *Quiet Alpha Wave*\n\n"
            f"🎯 *{trade['option_symbol']}*\n"
            f"💵 *Entry:* `${entry:.2f}`\n"
            f"🚀 *Current:* `${current_price:.2f}`\n"
            f"📈 *Extension:* الصفقة تجاوزت +100%\n"
            f"━━━━━━━━━━━━━━\n"
            f"💡 *Action*\n"
            f"- لا دخول متأخر\n"
            f"- فقط إدارة المتبقي من الصفقة\n"
            f"- شددي الوقف تدريجيًا\n"
            f"━━━━━━━━━━━━━━\n"
            f"🧠 *Quiet Alpha Insight:* دع الرابح يكمل... لكن بذكاء"
        )
        trade["wave_sent"] = True

    if trade["last_tp"] >= 3 and not trade.get("final_sent", False):
        max_gain = ((trade["highest"] - entry) / entry) * 100
        send_msg(
            f"🏁 *Quiet Alpha Result*\n\n"
            f"🎯 *{trade['option_symbol']}*\n"
            f"💵 *Entry:* `${entry:.2f}`\n"
            f"📈 *Highest Price:* `${trade['highest']:.2f}`\n"
            f"🚀 *Max Gain:* `{max_gain:.1f}%`\n"
            f"⏱️ *Elapsed Time:* `{get_duration(trade['start'])}`\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ Trade completed\n"
            f"🛡️ Risk controlled\n"
            f"🤍 شكرًا لثقتكم بـ Quiet Alpha"
        )
        trade["final_sent"] = True

    if current_price <= trade["stop"]:
        pnl = ((current_price - entry) / entry) * 100
        duration = get_duration(trade["start"])
        result_emoji = "✅" if pnl >= 0 else "⚠️"

        send_msg(
            f"{result_emoji} *Quiet Alpha Exit Notice*\n\n"
            f"🎯 *{trade['option_symbol']}*\n"
            f"💵 *Entry:* `${entry:.2f}`\n"
            f"📉 *Exit:* `${current_price:.2f}`\n"
            f"📊 *P/L:* `{pnl:.1f}%`\n"
            f"⏱️ *Duration:* `{duration}`\n"
            f"━━━━━━━━━━━━━━\n"
            f"🛡️ *Status:* تم تنفيذ الخروج وفق الخطة\n"
            f"🧠 *Note:* حماية رأس المال جزء من النجاح"
        )
        trade["closed"] = True


# =========================
# UW FETCH
# =========================
def fetch_uw_alerts():
    if not UW_API_KEY:
        print("❌ UW_API_KEY missing.")
        return []

    headers = {
        "Authorization": f"Bearer {UW_API_KEY}",
        "Accept": "application/json",
    }

    try:
        res = requests.get(
            "https://api.unusualwhales.com/api/alerts",
            headers=headers,
            timeout=15,
        )

        print("UW Status:", res.status_code)
        if res.status_code != 200:
            print("UW Response:", res.text[:300])
            return []

        data = res.json()
        if isinstance(data, dict):
            trades = data.get("data", []) or data.get("results", []) or []
        elif isinstance(data, list):
            trades = data
        else:
            trades = []

        print("Fetched trades:", len(trades))
        return trades

    except Exception as e:
        print("UW Fetch Error:", repr(e))
        return []


# =========================
# FLOW MONITOR
# =========================
def monitor_flow():
    global latest_trades

    print("🚀 Quiet Alpha matching engine started...")

    while True:
        trades = fetch_uw_alerts()
        latest_trades = trades[:50]

        for trade in trades[:50]:
            try:
                process_whale_trade(trade)
            except Exception as e:
                print("process_whale_trade error:", repr(e))

        time.sleep(POLL_SECONDS)


# =========================
# ACTIVE TRADE TRACKER
# =========================
def track_active_trades():
    global latest_trades

    while True:
        try:
            current_trades = latest_trades[:50]

            for trade in current_trades:
                try:
                    option_symbol = str(
                        trade.get("option_symbol") or trade.get("contract") or ""
                    ).strip().upper()

                    if not option_symbol:
                        continue

                    current_price = safe_float(
                        trade.get("price") or trade.get("mark") or trade.get("last")
                    )

                    if current_price <= 0:
                        continue

                    for trade_id, active in list(active_trades.items()):
                        if active.get("closed"):
                            continue

                        if str(active.get("option_symbol", "")).strip().upper() == option_symbol:
                            check_targets(trade_id, current_price)

                except Exception as e:
                    print(f"⚠️ Error in inner trade loop: {repr(e)}")

        except Exception as e:
            print(f"❌ track active trade error: {repr(e)}")

        time.sleep(5)


# =========================
# MAIN
# =========================
def main():
    if not TELEGRAM_TOKEN or not CHAT_ID or not UW_API_KEY:
        print("❌ Missing env vars: BOT_TOKEN / SIGNAL_CHAT_ID / UW_API_KEY")
        return

    send_msg(
        "🚀 *Quiet Alpha Engine Online*\n\n"
        "━━━━━━━━━━━━━━\n"
        "📡 النظام متصل الآن\n"
        "🐋 مراقبة تدفقات الحيتان فعالة\n"
        "📊 انتظار تطابق الشارت مع السيولة\n"
        "━━━━━━━━━━━━━━\n"
        "🤍 جاهزون لأول إشارة"
    )

    Thread(target=monitor_flow, daemon=True).start()
    Thread(target=track_active_trades, daemon=True).start()

    print(f"📡 App running on port {PORT}")
    app.run(host="0.0.0.0", port=int(PORT))


if __name__ == "__main__":
    main()

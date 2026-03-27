import os
import time
from threading import Thread
from datetime import datetime
from typing import Any, Dict, List, Set

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# =========================
# MARKET STATUS FROM TV
# =========================
market_status: Dict[str, str] = {
    "vix": "Neutral",
    "trend": "Neutral",
}

# =========================
# ENV
# =========================
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("SIGNAL_CHAT_ID")
UW_API_KEY = os.environ.get("UW_API_KEY")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))

TARGET_TICKERS = [
    t.strip().upper()
    for t in os.environ.get("TARGET_TICKERS", "SPX,SPXW").split(",")
    if t.strip()
]

# منع التكرار داخل الجلسة
seen_trade_ids: Set[str] = set()


# =========================
# HELPERS
# =========================
def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "N/A"):
            return default
        return int(float(value))
    except Exception:
        return default


def normalize_option_type(value: Any) -> str:
    text = str(value or "").upper().strip()
    if text in ("CALL", "C"):
        return "CALL"
    if text in ("PUT", "P"):
        return "PUT"
    return text


def build_trade_id(trade: Dict[str, Any]) -> str:
    return str(
        trade.get("id")
        or trade.get("_id")
        or trade.get("uuid")
        or trade.get("option_symbol")
        or trade.get("contract")
        or f"{trade.get('ticker')}_{trade.get('strike')}_{trade.get('price')}_{trade.get('timestamp')}"
    )


def extract_ticker(trade: Dict[str, Any]) -> str:
    return str(
        trade.get("ticker")
        or trade.get("symbol")
        or trade.get("underlying")
        or ""
    ).upper()


def extract_option_symbol(trade: Dict[str, Any]) -> str:
    return str(
        trade.get("option_symbol")
        or trade.get("contract")
        or "N/A"
    )


def extract_strike(trade: Dict[str, Any]) -> Any:
    return (
        trade.get("strike")
        or (trade.get("option") or {}).get("strike")
        or "N/A"
    )


def extract_open_interest(trade: Dict[str, Any]) -> int:
    return safe_int(
        trade.get("open_interest")
        or trade.get("oi")
        or (trade.get("option") or {}).get("open_interest")
    )


def extract_volume(trade: Dict[str, Any]) -> int:
    return safe_int(
        trade.get("volume")
        or trade.get("size")
        or (trade.get("option") or {}).get("volume")
    )


def extract_price(trade: Dict[str, Any]) -> float:
    return safe_float(
        trade.get("price")
        or trade.get("mark")
        or trade.get("last")
        or (trade.get("option") or {}).get("price")
        or (trade.get("option") or {}).get("mark")
        or (trade.get("option") or {}).get("last")
    )


def extract_premium(trade: Dict[str, Any], price: float, volume: int) -> float:
    premium = safe_float(
        trade.get("premium")
        or trade.get("value")
        or trade.get("total_premium")
        or trade.get("notional")
        or trade.get("transaction_value")
    )

    if premium > 0:
        return premium

    if price > 0 and volume > 0:
        return price * volume * 100

    return 0.0


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

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Webhook Update: {market_status}")
    return jsonify({"status": "ok", "market_status": market_status}), 200


# =========================
# TELEGRAM
# =========================
def send_msg(text: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram config missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    tv_url = "https://www.tradingview.com/chart/?symbol=CBOE%3ASPX"

    keyboard = {
        "inline_keyboard": [
            [{"text": "📊 فتح شارت SPX", "url": tv_url}]
        ]
    }

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": keyboard,
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        print("Telegram Status:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram Error:", repr(e))


# =========================
# UW FETCH
# =========================
def fetch_uw_alerts() -> List[Dict[str, Any]]:
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
            timeout=10
        )

        print("UW Status:", res.status_code)

        if res.status_code != 200:
            print("UW Response:", res.text[:300])
            return []

        data = res.json()

        if isinstance(data, dict):
            alerts = data.get("data", []) or data.get("results", []) or []
        elif isinstance(data, list):
            alerts = data
        else:
            alerts = []

        print("Fetched trades:", len(alerts))
        return alerts

    except Exception as e:
        print("UW Fetch Error:", repr(e))
        return []


# =========================
# SCORING
# =========================
def score_trade(
    premium: float,
    opt_type: str,
    trend: str,
    vix: str,
) -> int:
    score = 5

    # سيولة قوية
    if premium > 100000:
        score += 1
    if premium > 300000:
        score += 1
    if premium > 500000:
        score += 1

    # توافق مع الاتجاه
    if trend == "Up" and opt_type == "CALL":
        score += 2
    if trend == "Down" and opt_type == "PUT":
        score += 2

    # VIX عالي يدعم الـ PUT
    if vix == "High" and opt_type == "PUT":
        score += 1

    return score


# =========================
# MESSAGE
# =========================
def format_alert_message(
    ticker: str,
    option_symbol: str,
    strike: Any,
    opt_type: str,
    price: float,
    premium: float,
    score: int,
    volume: int,
    open_interest: int,
) -> str:
    tp_50 = price * 1.5
    tp_100 = price * 2.0

    return (
        f"🚨 *إشارة {ticker} قوية ({score}/10)* 🚨\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 العقد: `{option_symbol}`\n"
        f"🧱 السترايك: `{strike}`\n"
        f"🔥 النوع: `{opt_type}`\n"
        f"💵 سعر الحوت: `${price:.2f}`\n"
        f"💰 السيولة: `${premium:,.0f}`\n"
        f"📊 الحجم/OI: `{volume}/{open_interest}`\n"
        f"📈 حالة السوق: `{market_status['trend']} | VIX: {market_status['vix']}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"🚀 *أهداف الدبل المتوقعة:*\n"
        f"✅ +50%: `${tp_50:.2f}`\n"
        f"💎 +100%: `${tp_100:.2f}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"💡 *ملاحظة:* راقب الاتجاه في TradingView قبل الدخول!"
    )


# =========================
# CORE LOGIC
# =========================
def monitor_flow() -> None:
    print(f"🚀 البوت بدأ مراقبة تدفقات {TARGET_TICKERS}...")

    while True:
        alerts = fetch_uw_alerts()

        for trade in alerts[:50]:
            try:
                # اطبعي الصفقة إذا تبين تشخيص أعمق
                # print(trade)

                ticker = extract_ticker(trade)
                if not ticker:
                    continue

                if not any(target in ticker for target in TARGET_TICKERS):
                    continue

                trade_id = build_trade_id(trade)
                if trade_id in seen_trade_ids:
                    continue

                price = extract_price(trade)

                # فلتر سعر أوسع مؤقتًا
                if not (2.0 <= price <= 15.0):
                    continue

                opt_type = normalize_option_type(
                    trade.get("option_type")
                    or trade.get("operation_type")
                    or trade.get("type")
                    or trade.get("side")
                )
                if opt_type not in ("CALL", "PUT"):
                    continue

                option_symbol = extract_option_symbol(trade)
                strike = extract_strike(trade)
                volume = extract_volume(trade)
                open_interest = extract_open_interest(trade)
                premium = extract_premium(trade, price, volume)

                score = score_trade(
                    premium=premium,
                    opt_type=opt_type,
                    trend=market_status["trend"],
                    vix=market_status["vix"],
                )

                # خففنا الشرط
                if score < 7:
                    continue

                msg = format_alert_message(
                    ticker=ticker,
                    option_symbol=option_symbol,
                    strike=strike,
                    opt_type=opt_type,
                    price=price,
                    premium=premium,
                    score=score,
                    volume=volume,
                    open_interest=open_interest,
                )

                send_msg(msg)
                seen_trade_ids.add(trade_id)
                print(f"✅ تم إرسال تنبيه {ticker} {strike} {opt_type}")

            except Exception as e:
                print("Trade Analyze Error:", repr(e))
                continue

        time.sleep(POLL_SECONDS)


# =========================
# MAIN
# =========================
def main() -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID or not UW_API_KEY:
        print("❌ متغيرات أساسية ناقصة: BOT_TOKEN / SIGNAL_CHAT_ID / UW_API_KEY")
        return

    Thread(target=monitor_flow, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))


if __name__ == "__main__":
    main()

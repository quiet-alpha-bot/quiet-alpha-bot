import os
import time
import requests
from flask import Flask, request, jsonify
from threading import Thread
from datetime import datetime

app = Flask(__name__)

# --- الإعدادات والمتغيرات ---
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("SIGNAL_CHAT_ID")
UW_API_KEY = os.environ.get("UW_API_KEY")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))
TARGET_TICKERS = ["SPX"]

# سجل لمنع التكرار
seen_trade_ids = set()

# حالة السوق (يتم تحديثها عبر TradingView)
market_status = {"vix": "Neutral", "trend": "Neutral"}


# --- 1. استقبال التنبيهات من TradingView (Webhook) ---
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    if "vix" in data:
        market_status["vix"] = str(data["vix"])
    if "trend" in data:
        market_status["trend"] = str(data["trend"])

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Webhook Update: {market_status}")
    return jsonify({"status": "ok", "market_status": market_status}), 200


# --- 2. دوال المساعدة والإرسال ---
def send_msg(text: str):
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
        "reply_markup": keyboard
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram status:", r.status_code, r.text[:200])
    except Exception as e:
        print(f"Telegram Error: {e}")


def send_welcome_message():
    now = datetime.now().strftime('%H:%M:%S')
    msg = (
        f"🚀 *تم تشغيل رادار SPX بنجاح!*\n"
        f"━━━━━━━━━━━━━━\n"
        f"⏰ الوقت: `{now}`\n"
        f"📊 النطاق السعري الحالي: `$2.0 - $15.0`\n"
        f"🎯 حالة السوق الحالية: `{market_status['trend']} | VIX: {market_status['vix']}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"💡 *أنا الآن أراقب تدفقات Unusual Whales بالنيابة عنكِ...*"
    )
    send_msg(msg)


# --- 3. محرك تحليل السيولة ---
def score_trade(premium, opt_type, trend, vix):
    score = 6
    if premium > 500000:
        score += 1
    if trend == "Up" and opt_type == "CALL":
        score += 2
    if trend == "Down" and opt_type == "PUT":
        score += 1
    if vix == "High" and opt_type == "PUT":
        score += 1
    return score


def normalize_option_type(raw):
    raw = str(raw or "").upper()
    if raw in ("C", "CALL"):
        return "CALL"
    if raw in ("P", "PUT"):
        return "PUT"
    return ""


def monitor_flow():
    print(f"🚀 البوت بدأ مراقبة تدفقات {TARGET_TICKERS}...")

    while True:
        headers = {"Authorization": f"Bearer {UW_API_KEY}"}

        try:
            res = requests.get(
                "https://api.unusualwhales.com/api/alerts",
                headers=headers,
                timeout=15
            )

            if res.status_code != 200:
                print("UW status:", res.status_code, res.text[:200])
                time.sleep(POLL_SECONDS)
                continue

            data = res.json()
            trades = data.get("data", []) if isinstance(data, dict) else []

            print("Fetched trades:", len(trades))

            for t in trades:
                ticker = str(
                    t.get("ticker")
                    or t.get("symbol")
                    or t.get("underlying")
                    or ""
                ).upper()

                if ticker not in TARGET_TICKERS and "SPX" not in ticker:
                    continue

                trade_id = str(
                    t.get("id")
                    or t.get("_id")
                    or t.get("uuid")
                    or t.get("option_symbol")
                    or t.get("contract")
                    or f"{ticker}_{t.get('strike')}_{t.get('price')}_{t.get('timestamp')}"
                )

                if trade_id in seen_trade_ids:
                    continue

                price = float(
                    t.get("price")
                    or t.get("mark")
                    or t.get("last")
                    or 0
                )

                if not (2.0 <= price <= 15.0):
                    continue

                opt = normalize_option_type(
                    t.get("option_type")
                    or t.get("operation_type")
                    or t.get("type")
                    or t.get("side")
                )
                if opt not in ("CALL", "PUT"):
                    continue

                prem = float(
                    t.get("premium")
                    or t.get("value")
                    or t.get("total_premium")
                    or t.get("notional")
                    or 0
                )

                strike = t.get("strike") or "N/A"
                option_symbol = t.get("option_symbol") or t.get("contract") or f"SPX {strike} {opt}"

                score = score_trade(prem, opt, market_status["trend"], market_status["vix"])

                if score >= 7:
                    tp_50 = price * 1.5
                    tp_100 = price * 2.0

                    msg = (
                        f"🚨 *إشارة SPX قوية ({score}/10)* 🚨\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🎯 العقد: `{option_symbol}`\n"
                        f"🧱 السترايك: `{strike}`\n"
                        f"🔥 النوع: `{opt}`\n"
                        f"💵 سعر الحوت: `${price:.2f}`\n"
                        f"💰 السيولة: `${prem:,.0f}`\n"
                        f"📈 حالة السوق: `{market_status['trend']} | VIX: {market_status['vix']}`\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🚀 *أهداف الدبل المتوقعة:*\n"
                        f"✅ +50%: `${tp_50:.2f}`\n"
                        f"💎 +100%: `${tp_100:.2f}`\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"💡 *ملاحظة:* راقبي الاتجاه في TradingView قبل الدخول!"
                    )

                    send_msg(msg)
                    seen_trade_ids.add(trade_id)
                    print(f"✅ تم إرسال تنبيه {ticker} {strike} {opt}")

        except Exception as e:
            print(f"Monitor Flow Error: {e}")

        time.sleep(POLL_SECONDS)


# --- MAIN ---
def main():
    if not TELEGRAM_TOKEN or not CHAT_ID or not UW_API_KEY:
        print("❌ متغيرات أساسية ناقصة: BOT_TOKEN / SIGNAL_CHAT_ID / UW_API_KEY")
        return

    send_welcome_message()
    Thread(target=monitor_flow, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))


if __name__ == "__main__":
    main()

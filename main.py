import os
import re
import math
from datetime import datetime
from telegram import Bot
from openai import OpenAI

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not CHAT_ID:
    raise ValueError("SIGNAL_CHAT_ID is missing")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is missing")

bot = Bot(token=BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        value = str(value).replace(",", "").replace("$", "").strip()
        return float(value)
    except Exception:
        return default


def parse_contract(contract_text: str) -> dict:
    """
    Example:
    SPXW 24 MAR 2026 5200 CALL
    SPXW 24 MAR 2026 5200 PUT
    """
    text = contract_text.upper().strip()

    side = "CALL" if "CALL" in text else "PUT" if "PUT" in text else "UNKNOWN"

    strike_match = re.search(r"\b(\d{3,5})\b(?=\s+(CALL|PUT))", text)
    strike = strike_match.group(1) if strike_match else "N/A"

    dte = "0DTE" if "SPXW" in text else "1DTE"

    return {
        "symbol": "SPXW" if "SPXW" in text else "SPX",
        "side": side,
        "strike": strike,
        "dte_label": dte,
    }


def premium_score(premium: float) -> int:
    if premium >= 500000:
        return 25
    if premium >= 300000:
        return 20
    if premium >= 200000:
        return 14
    if premium >= 100000:
        return 8
    return 0


def size_score(size: float) -> int:
    if size >= 1000:
        return 15
    if size >= 500:
        return 10
    if size >= 200:
        return 6
    return 0


def oi_score(open_interest: float) -> int:
    if open_interest >= 3000:
        return 10
    if open_interest >= 1000:
        return 7
    if open_interest >= 500:
        return 4
    return 0


def ratio_score(volume_oi_ratio: float) -> int:
    if volume_oi_ratio >= 2.5:
        return 15
    if volume_oi_ratio >= 1.5:
        return 10
    if volume_oi_ratio >= 1.0:
        return 5
    return 0


def price_quality_score(price: float) -> int:
    if 1.5 <= price <= 4.99:
        return 10
    if 5 <= price <= 10:
        return 8
    if 0.5 <= price <= 1.49:
        return 4
    if price > 10:
        return 5
    return 0


def dte_score(days_to_expiry: int) -> int:
    if days_to_expiry == 0:
        return 10
    if days_to_expiry == 1:
        return 7
    return 0


def direction_score(side_hint: str) -> int:
    hint = (side_hint or "").lower()
    if any(x in hint for x in ["ask", "bullish", "opening buy", "sweep"]):
        return 15
    if any(x in hint for x in ["mid", "mixed"]):
        return 7
    if any(x in hint for x in ["bid", "weak"]):
        return 3
    return 7


def grade_score(score: int) -> tuple[str, str]:
    if score >= 85:
        return "A+ ELITE", "HIGH"
    if score >= 75:
        return "A STRONG", "MEDIUM-HIGH"
    if score >= 65:
        return "B WATCHLIST", "WATCH ONLY"
    return "REJECT", "LOW"


def build_targets(entry: float) -> tuple[float, float, float]:
    tp1 = round(entry * 1.30, 2)
    tp2 = round(entry * 1.50, 2)
    tp3 = round(entry * 2.00, 2)
    return tp1, tp2, tp3


def build_extensions(entry: float) -> tuple[float, float, float]:
    ext1 = round(entry * 2.40, 2)
    ext2 = round(entry * 3.00, 2)
    ext3 = round(entry * 3.80, 2)
    return ext1, ext2, ext3


def build_stop(entry: float) -> float:
    if 0.5 <= entry <= 2.0:
        return round(entry * 0.75, 2)
    if 2.01 <= entry <= 5.0:
        return round(entry * 0.70, 2)
    return round(entry * 0.65, 2)


def passes_initial_filter(data: dict) -> bool:
    return all(
        [
            data["symbol"] == "SPXW",
            data["days_to_expiry"] in (0, 1),
            data["premium"] >= 100000,
            data["size"] >= 200,
            data["volume"] >= 100,
            data["open_interest"] >= 500,
            data["volume_oi_ratio"] >= 1.0,
            0.5 <= data["entry_price"] <= 20.0,
        ]
    )


def score_signal(data: dict) -> int:
    total = 0
    total += premium_score(data["premium"])
    total += size_score(data["size"])
    total += oi_score(data["open_interest"])
    total += ratio_score(data["volume_oi_ratio"])
    total += price_quality_score(data["entry_price"])
    total += dte_score(data["days_to_expiry"])
    total += direction_score(data.get("side_hint", ""))
    return total


def ai_reason_summary(data: dict, grade: str, score: int) -> str:
    prompt = f"""
You are generating a short, professional options-flow reason.
Keep it to one line only.

Data:
Symbol: {data['symbol']}
Side: {data['side']}
Strike: {data['strike']}
Premium: {data['premium']}
Size: {data['size']}
Open Interest: {data['open_interest']}
Volume: {data['volume']}
Volume/OI Ratio: {data['volume_oi_ratio']}
Entry Price: {data['entry_price']}
Days To Expiry: {data['days_to_expiry']}
Grade: {grade}
Score: {score}

Return a concise reason like:
Whale flow + strong premium + aggressive momentum
"""
    try:
        response = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
        )
        text = response.output_text.strip()
        return text if text else "Whale flow + strong premium + aggressive momentum"
    except Exception:
        return "Whale flow + strong premium + aggressive momentum"


async def send_signal(data: dict):
    score = score_signal(data)
    grade, confidence = grade_score(score)

    if grade not in ("A+ ELITE", "A STRONG"):
        print(f"Signal rejected: {grade} ({score})")
        return

    tp1, tp2, tp3 = build_targets(data["entry_price"])
    stop_price = build_stop(data["entry_price"])
    reason = ai_reason_summary(data, grade, score)

    message = f"""🔥 Quiet Alpha Signal

{data['symbol']} {data['dte_label']} {data['side']}
Strike: {data['strike']}
Entry: {data['entry_price']:.2f}

Confidence: {confidence}
Grade: {grade}
Score: {score}/100

🎯 Targets:
TP1: {tp1}
TP2: {tp2}
TP3: {tp3}

⚠️ Stop:
{stop_price}

🧠 Reason:
{reason}
"""

    await bot.send_message(chat_id=CHAT_ID, text=message)


async def send_extension(data: dict):
    score = score_signal(data)
    grade, confidence = grade_score(score)

    if grade != "A+ ELITE":
        return

    ext1, ext2, ext3 = build_extensions(data["entry_price"])

    message = f"""🌊 Quiet Alpha Extension

{data['symbol']} {data['dte_label']} {data['side']}
Strike: {data['strike']}
Price: {data['entry_price']:.2f}

Confidence: {confidence}
Extension Mode: ACTIVE

🎯 New Targets:
EXT1: {ext1}
EXT2: {ext2}
EXT3: {ext3}

Trail your stop — do not rush exit
حرّك وقفك — لا تستعجل الخروج
"""
    await bot.send_message(chat_id=CHAT_ID, text=message)


async def send_weakening_alert():
    message = """⚠️ Quiet Alpha Alert

Momentum weakening
الزخم بدأ يضعف

Liquidity decreasing
السيولة تقل

Price slowing near resistance
السعر يتباطأ قرب الهدف

Consider securing profits
يفضل تأمين الأرباح
"""
    await bot.send_message(chat_id=CHAT_ID, text=message)


async def send_smart_exit():
    message = """🧠 Quiet Alpha — Smart Exit

Decision: 🔴 Take Profit
يفضل الخروج أو تأمين الربح

Based on liquidity & momentum
بناءً على السيولة والزخم

Manage your trade wisely
إدارة الصفقة مسؤوليتك
"""
    await bot.send_message(chat_id=CHAT_ID, text=message)


async def demo_run():
    """
    هذه نسخة تشغيل أولية.
    لاحقًا بنبدل demo data بقراءة الإيميل من Unusual Whales.
    """
    raw_contract = "SPXW 24 MAR 2026 5200 CALL"
    parsed = parse_contract(raw_contract)

    signal_data = {
        "symbol": parsed["symbol"],
        "side": parsed["side"],
        "strike": parsed["strike"],
        "dte_label": parsed["dte_label"],
        "days_to_expiry": 0,
        "premium": 640000,
        "size": 1200,
        "open_interest": 2500,
        "volume": 1800,
        "volume_oi_ratio": 1.8,
        "entry_price": 3.80,
        "side_hint": "ask-side bullish sweep opening buy",
    }

    if not passes_initial_filter(signal_data):
        print("Initial filter failed.")
        return

    await send_signal(signal_data)
    await send_extension(signal_data)
    await send_weakening_alert()
    await send_smart_exit()


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo_run())

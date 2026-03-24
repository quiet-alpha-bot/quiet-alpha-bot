import os
import asyncio
from datetime import datetime
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")


def calculate_stop(entry: float, stop_pct: float = 0.30) -> float:
    """Stop loss = 30% below entry by default."""
    return round(entry * (1 - stop_pct), 2)


def calculate_targets(entry: float, magnet_move_points: float, strength: str = "HIGH") -> tuple[float, float, float]:
    """
    Simplified contract targets based on magnet distance.
    This is a practical starter model for your private testing phase.
    """
    if strength.upper() == "HIGH":
        mult1, mult2, mult3 = 0.06, 0.14, 0.21
    else:
        mult1, mult2, mult3 = 0.05, 0.11, 0.17

    tp1 = round(entry + (magnet_move_points * mult1), 2)
    tp2 = round(entry + (magnet_move_points * mult2), 2)
    tp3 = round(entry + (magnet_move_points * mult3), 2)
    return tp1, tp2, tp3


async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")
    if not CHAT_ID:
        raise ValueError("SIGNAL_CHAT_ID is missing")

    bot = Bot(token=BOT_TOKEN)

    # -----------------------------
    # Demo trade data (temporary)
    # Later we replace this with UW API
    # -----------------------------
    symbol = "SPXW"
    strike = 5200
    option_type = "CALL"
    entry = 3.80
    now_30 = 4.94
    now_50 = 5.70
    now_70 = 6.46
    now_100 = 7.60

    premium = "640K"
    confidence = "HIGH"
    magnet = 5215
    current_price = 5193

    # Difference between current index price and magnet
    magnet_move_points = abs(magnet - current_price)

    stop = calculate_stop(entry, stop_pct=0.30)
    tp1, tp2, tp3 = calculate_targets(entry, magnet_move_points, strength=confidence)

    entry_time = datetime.now()

    # 1) TRADE ALERT
    await bot.send_message(
        chat_id=int(CHAT_ID),
        text=f"""🚨 Quiet Alpha Trade Alert
تنبيه صفقة — Quiet Alpha

{symbol} {strike} {option_type}

Entry: {entry}
سعر الدخول: {entry}

Stop: {stop}
وقف الخسارة: {stop}

Magnet: {magnet}
المغناطيس: {magnet}

Confidence: {confidence}
قوة الصفقة: {"عالية" if confidence == "HIGH" else "جيدة"}

Premium: {premium}
السيولة: {premium}

Targets:
TP1: {tp1}
TP2: {tp2}
TP3: {tp3}

الأهداف:
الهدف 1: {tp1}
الهدف 2: {tp2}
الهدف 3: {tp3}"""
    )

    await asyncio.sleep(5)

    # 2) +30%
    await bot.send_message(
        chat_id=int(CHAT_ID),
        text=f"""📈 Quiet Alpha Update
تحديث كوايت ألفا

+30% ✅

Entry: {entry}
سعر الدخول: {entry}

Now: {now_30}
السعر الآن: {now_30}

TP1 approaching
الهدف الأول قريب

Small accounts: consider taking profit
Large accounts: raise your stop

محافظ صغيرة: يفضّل جني الربح
محافظ كبيرة: ارفع وقفك"""
    )

    await asyncio.sleep(5)

    # 3) +50%
    await bot.send_message(
        chat_id=int(CHAT_ID),
        text=f"""📈 Quiet Alpha Update
تحديث كوايت ألفا

+50% 🔥

Entry: {entry}
سعر الدخول: {entry}

Now: {now_50}
السعر الآن: {now_50}

Raise stop to +20%
ارفع وقفك إلى +20%"""
    )

    await asyncio.sleep(5)

    # 4) +70%
    await bot.send_message(
        chat_id=int(CHAT_ID),
        text=f"""📈 Quiet Alpha Update
تحديث كوايت ألفا

+70% ✨

Entry: {entry}
سعر الدخول: {entry}

Now: {now_70}
السعر الآن: {now_70}

Raise stop to +40%
ارفع وقفك إلى +40%

Trade is moving in your favor
الصفقة تسير لصالحك"""
    )

    await asyncio.sleep(5)

    # 5) +100%
    duration_minutes = int((datetime.now() - entry_time).total_seconds() // 60)
    if duration_minutes == 0:
        duration_text_en = "Less than 1 minute"
        duration_text_ar = "أقل من دقيقة"
    else:
        duration_text_en = f"{duration_minutes}m"
        duration_text_ar = f"{duration_minutes} دقيقة"

    await bot.send_message(
        chat_id=int(CHAT_ID),
        text=f"""🎯 Quiet Alpha
كوايت ألفا

+100% 🎉

Entry: {entry}
سعر الدخول: {entry}

Now: {now_100}
السعر الآن: {now_100}

Duration: {duration_text_en}
مدة الصفقة: {duration_text_ar}

Successful trade
صفقة ناجحة

Continuation is your decision
الاستمرار بالصفقة قرارك"""
    )


if __name__ == "__main__":
    asyncio.run(main())

import os
import asyncio
from datetime import datetime
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")


def calculate_stop(entry: float, stop_pct: float = 0.30) -> float:
    return round(entry * (1 - stop_pct), 2)


def calculate_targets(entry: float, magnet_move_points: float, strength: str = "HIGH") -> tuple[float, float, float]:
    if strength.upper() == "HIGH":
        mult1, mult2, mult3 = 0.06, 0.14, 0.21
    else:
        mult1, mult2, mult3 = 0.05, 0.11, 0.17

    tp1 = round(entry + (magnet_move_points * mult1), 2)
    tp2 = round(entry + (magnet_move_points * mult2), 2)
    tp3 = round(entry + (magnet_move_points * mult3), 2)
    return tp1, tp2, tp3


def format_duration(seconds: int) -> tuple[str, str]:
    if seconds < 60:
        return f"{seconds} seconds", f"{seconds} ثانية"
    minutes = seconds // 60
    return f"{minutes} minute{'s' if minutes > 1 else ''}", f"{minutes} دقيقة"


async def send_trade_alert(bot: Bot, chat_id: int, symbol: str, strike: int, option_type: str,
                           entry: float, stop: float, magnet: int, confidence: str,
                           premium: str, tp1: float, tp2: float, tp3: float) -> None:
    await bot.send_message(
        chat_id=chat_id,
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


async def send_update_30(bot: Bot, chat_id: int, entry: float, now_price: float) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"""📈 Quiet Alpha Update
تحديث كوايت ألفا

+30% ✅

Entry: {entry}
سعر الدخول: {entry}

Now: {now_price}
السعر الآن: {now_price}

TP1 approaching
الهدف الأول قريب

Small accounts: consider taking profit
Large accounts: raise your stop

محافظ صغيرة: يفضّل جني الربح
محافظ كبيرة: ارفع وقفك"""
    )


async def send_update_50(bot: Bot, chat_id: int, entry: float, now_price: float) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"""📈 Quiet Alpha Update
تحديث كوايت ألفا

+50% 🔥

Entry: {entry}
سعر الدخول: {entry}

Now: {now_price}
السعر الآن: {now_price}

Raise stop to +20%
ارفع وقفك إلى +20%"""
    )


async def send_update_70(bot: Bot, chat_id: int, entry: float, now_price: float) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"""📈 Quiet Alpha Update
تحديث كوايت ألفا

+70% ✨

Entry: {entry}
سعر الدخول: {entry}

Now: {now_price}
السعر الآن: {now_price}

Raise stop to +40%
ارفع وقفك إلى +40%

Trade is moving in your favor
الصفقة تسير لصالحك"""
    )


async def send_update_100(bot: Bot, chat_id: int, entry: float, now_price: float, duration_seconds: int) -> None:
    duration_en, duration_ar = format_duration(duration_seconds)

    await bot.send_message(
        chat_id=chat_id,
        text=f"""🎯 Quiet Alpha
كوايت ألفا

+100% 🎉

Entry: {entry}
سعر الدخول: {entry}

Now: {now_price}
السعر الآن: {now_price}

Duration: {duration_en}
مدة الصفقة: {duration_ar}

Successful trade
صفقة ناجحة

Continuation is your decision
الاستمرار بالصفقة قرارك"""
    )


async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")
    if not CHAT_ID:
        raise ValueError("SIGNAL_CHAT_ID is missing")

    bot = Bot(token=BOT_TOKEN)
    chat_id = int(CHAT_ID)

    # Demo trade data for internal testing
    symbol = "SPXW"
    strike = 5200
    option_type = "CALL"
    entry = 3.80
    premium = "640K"
    confidence = "HIGH"
    magnet = 5215
    current_price = 5193

    # Demo progress prices
    price_30 = 4.94
    price_50 = 5.70
    price_70 = 6.46
    price_100 = 7.60

    magnet_move_points = abs(magnet - current_price)
    stop = calculate_stop(entry, stop_pct=0.30)
    tp1, tp2, tp3 = calculate_targets(entry, magnet_move_points, strength=confidence)

    entry_time = datetime.now()

    await send_trade_alert(
        bot, chat_id, symbol, strike, option_type,
        entry, stop, magnet, confidence, premium, tp1, tp2, tp3
    )

    await asyncio.sleep(8)
    await send_update_30(bot, chat_id, entry, price_30)

    await asyncio.sleep(8)
    await send_update_50(bot, chat_id, entry, price_50)

    await asyncio.sleep(8)
    await send_update_70(bot, chat_id, entry, price_70)

    await asyncio.sleep(8)
    duration_seconds = int((datetime.now() - entry_time).total_seconds())
    await send_update_100(bot, chat_id, entry, price_100, duration_seconds)


if __name__ == "__main__":
    asyncio.run(main())

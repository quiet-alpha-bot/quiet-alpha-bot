import os
import asyncio
from datetime import datetime
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("SIGNAL_CHAT_ID")


def calculate_stop(entry: float, stop_pct: float = 0.30) -> float:
    return round(entry * (1 - stop_pct), 2)


def calculate_targets(entry: float, magnet_move_points: float, strength: str = "HIGH") -> tuple[float, float, float]:
    if strength.upper() in {"HIGH", "MONSTER"}:
        mult1, mult2, mult3 = 0.06, 0.14, 0.21
    else:
        mult1, mult2, mult3 = 0.05, 0.11, 0.17

    tp1 = round(entry + (magnet_move_points * mult1), 2)
    tp2 = round(entry + (magnet_move_points * mult2), 2)
    tp3 = round(entry + (magnet_move_points * mult3), 2)
    return tp1, tp2, tp3


def calculate_extension_targets(current_price: float) -> tuple[float, float, float]:
    ext1 = round(current_price * 1.20, 2)
    ext2 = round(current_price * 1.40, 2)
    ext3 = round(current_price * 1.60, 2)
    return ext1, ext2, ext3


def format_duration(seconds: int) -> tuple[str, str]:
    if seconds < 60:
        return f"{seconds} seconds", f"{seconds} ثانية"
    minutes = seconds // 60
    return f"{minutes} minute{'s' if minutes > 1 else ''}", f"{minutes} دقيقة"


def is_valid_trade(symbol: str, premium: int, delta: float, expiry: str) -> bool:
    valid_symbol = symbol in {"SPX", "SPXW"}
    valid_premium = premium >= 300_000
    valid_delta = delta >= 0.35
    valid_expiry = expiry == "0DTE"
    return valid_symbol and valid_premium and valid_delta and valid_expiry


def calculate_score(premium: int, magnet_distance: int, delta: float) -> int:
    score = 0

    if premium >= 1_000_000:
        score += 40
    elif premium >= 500_000:
        score += 30
    elif premium >= 300_000:
        score += 20

    if magnet_distance <= 10:
        score += 30
    elif magnet_distance <= 20:
        score += 20
    elif magnet_distance <= 35:
        score += 10

    if delta >= 0.50:
        score += 25
    elif delta >= 0.40:
        score += 20
    elif delta >= 0.35:
        score += 10

    return min(score, 99)


def confidence_label(score: int) -> str:
    if score >= 85:
        return "MONSTER"
    if score >= 70:
        return "HIGH"
    if score >= 55:
        return "MEDIUM"
    return "LOW"


def confidence_label_ar(score: int) -> str:
    if score >= 85:
        return "عالية جدًا"
    if score >= 70:
        return "عالية"
    if score >= 55:
        return "متوسطة"
    return "منخفضة"


def should_send_extension(score: int, current_price: float, tp3: float) -> bool:
    return score >= 60 and current_price > tp3


async def send_trade_alert(
    bot: Bot,
    chat_id: int,
    symbol: str,
    strike: int,
    option_type: str,
    entry: float,
    stop: float,
    magnet: int,
    premium_text: str,
    score: int,
    confidence_en: str,
    confidence_ar: str,
    tp1: float,
    tp2: float,
    tp3: float,
) -> None:
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

Score: {score}
تقييم الصفقة: {score}

Confidence: {confidence_en}
قوة الصفقة: {confidence_ar}

Targets:
TP1: {tp1}
TP2: {tp2}
TP3: {tp3}

الأهداف:
الهدف 1: {tp1}
الهدف 2: {tp2}
الهدف 3: {tp3}

Premium: {premium_text}
السيولة: {premium_text}"""
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

Approaching Target 1
اقتراب من الهدف الأول

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


async def send_extension_alert(bot: Bot, chat_id: int, now_price: float) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"""🌊 Quiet Alpha Extension
موجة كوايت ألفا

Price: {now_price}
السعر: {now_price}

Momentum remains strong
الزخم ما زال قويًا

Continuation remains possible
الامتداد لا يزال قائمًا

Trail your stop
حرّك وقفك مع الحركة"""
    )


async def send_extension_targets(bot: Bot, chat_id: int, ext1: float, ext2: float, ext3: float) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"""🎯 Quiet Alpha New Targets
الأهداف الجديدة — كوايت ألفا

EXT1: {ext1}
EXT2: {ext2}
EXT3: {ext3}

الهدف الممتد 1: {ext1}
الهدف الممتد 2: {ext2}
الهدف الممتد 3: {ext3}"""
    )


async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")
    if not CHAT_ID:
        raise ValueError("SIGNAL_CHAT_ID is missing")

    bot = Bot(token=BOT_TOKEN)
    chat_id = int(CHAT_ID)

    # Demo trade data
    symbol = "SPXW"
    strike = 5200
    option_type = "CALL"
    expiry = "0DTE"
    entry = 3.80
    premium = 640_000
    premium_text = "640K"
    delta = 0.41
    magnet = 5215
    current_price = 5193

    # Demo progress prices
    price_30 = 4.94
    price_50 = 5.70
    price_70 = 6.46
    price_100 = 7.60
    extension_price = 9.20

    if not is_valid_trade(symbol, premium, delta, expiry):
        print("Trade rejected by QA filter.")
        return

    magnet_distance = abs(magnet - current_price)
    score = calculate_score(premium, magnet_distance, delta)
    confidence_en = confidence_label(score)
    confidence_ar = confidence_label_ar(score)

    stop = calculate_stop(entry, stop_pct=0.30)
    tp1, tp2, tp3 = calculate_targets(entry, magnet_distance, strength=confidence_en)

    entry_time = datetime.now()

    await send_trade_alert(
        bot=bot,
        chat_id=chat_id,
        symbol=symbol,
        strike=strike,
        option_type=option_type,
        entry=entry,
        stop=stop,
        magnet=magnet,
        premium_text=premium_text,
        score=score,
        confidence_en=confidence_en,
        confidence_ar=confidence_ar,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
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

    await asyncio.sleep(8)

    if should_send_extension(score, extension_price, tp3):
        ext1, ext2, ext3 = calculate_extension_targets(extension_price)
        await send_extension_alert(bot, chat_id, extension_price)

        await asyncio.sleep(8)
        await send_extension_targets(bot, chat_id, ext1, ext2, ext3)


if __name__ == "__main__":
    asyncio.run(main())

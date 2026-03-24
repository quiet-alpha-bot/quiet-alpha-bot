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


def should_send_weakening(premium: int, prev_premium: int, current_price: float, tp3: float) -> bool:
    liquidity_drop = premium < prev_premium * 0.5
    near_target = current_price >= tp3 * 0.9
    return liquidity_drop and near_target


def calculate_exit_score(premium: int, prev_premium: int, current_price: float, tp3: float) -> int:
    score = 0

    # Current liquidity
    if premium > 500_000:
        score += 2
    elif premium > 300_000:
        score += 1
    else:
        score -= 1

    # Liquidity fading
    if premium < prev_premium:
        score -= 1

    # Close to major target
    if current_price >= tp3 * 0.9:
        score -= 2

    return score


def get_exit_decision(score: int) -> tuple[str, str]:
    if score >= 2:
        return "🟢 Strong Hold", "استمرار قوي"
    elif score >= 0:
        return "🟡 Hold", "استمر بحذر"
    else:
        return "🔴 Take Profit", "يفضل الخروج أو تأمين الربح"


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

Target 1 within reach
الهدف الأول قريب

Momentum building
الزخم يتزايد

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

Position secured
الصفقة أصبحت مؤمّنة

Adjust stop to +20%
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

Trend holding strong
الاتجاه ما زال قوي

Adjust stop to +40%
ارفع وقفك إلى +40%

Let it run with control
اتركها تتحرك مع إدارة"""
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

Execution complete
تم تنفيذ الصفقة بنجاح

Profit locked
الربح تحقق

Next move is yours
القرار الآن بيدك"""
    )


async def send_extension_alert(bot: Bot, chat_id: int, now_price: float) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"""🌊 Quiet Alpha Extension
موجة كوايت ألفا

Price: {now_price}
السعر: {now_price}

Strong continuation detected
امتداد قوي تم رصده

Market still pushing in same direction
السوق مستمر بنفس الاتجاه

Trail your stop — do not rush exit
حرّك وقفك — لا تتعجل الخروج"""
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
الهدف الممتد 3: {ext3}

Momentum phase active
مرحلة الزخم مستمرة"""
    )


async def send_weakening_alert(bot: Bot, chat_id: int) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text="""⚠️ Quiet Alpha Alert
تنبيه كوايت ألفا

Momentum weakening
الزخم بدأ يضعف

Liquidity decreasing
السيولة تقل

Price slowing near resistance
السعر يتباطأ قرب الهدف

Consider securing profits
يفضل تأمين الأرباح

Avoid overextension
تجنب الطمع"""
    )


async def send_exit_score(bot: Bot, chat_id: int, decision_en: str, decision_ar: str) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"""🧠 Quiet Alpha — Smart Exit

Decision: {decision_en}
{decision_ar}

Based on liquidity & momentum
بناءً على السيولة والزخم

Manage your trade wisely
إدارة الصفقة مسؤوليتك"""
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
    prev_premium = 900_000
    delta = 0.41
    magnet = 5215
    current_price = 5193

    # Demo progress prices
    price_30 = 4.94
    price_50 = 5.70
    price_70 = 6.46
    price_100 = 7.60
    extension_price = 9.20

    # Demo fading liquidity after extension
    fading_premium = 180_000

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

    # Smart exit after 50%
    exit_score_50 = calculate_exit_score(premium, prev_premium, price_50, tp3)
    decision_en_50, decision_ar_50 = get_exit_decision(exit_score_50)
    await asyncio.sleep(4)
    await send_exit_score(bot, chat_id, decision_en_50, decision_ar_50)

    await asyncio.sleep(8)
    await send_update_70(bot, chat_id, entry, price_70)

    # Smart exit after 70%
    exit_score_70 = calculate_exit_score(premium, prev_premium, price_70, tp3)
    decision_en_70, decision_ar_70 = get_exit_decision(exit_score_70)
    await asyncio.sleep(4)
    await send_exit_score(bot, chat_id, decision_en_70, decision_ar_70)

    await asyncio.sleep(8)
    duration_seconds = int((datetime.now() - entry_time).total_seconds())
    await send_update_100(bot, chat_id, entry, price_100, duration_seconds)

    await asyncio.sleep(8)

    if should_send_extension(score, extension_price, tp3):
        ext1, ext2, ext3 = calculate_extension_targets(extension_price)
        await send_extension_alert(bot, chat_id, extension_price)

        await asyncio.sleep(8)
        await send_extension_targets(bot, chat_id, ext1, ext2, ext3)

        # Weakening check after extension
        if should_send_weakening(fading_premium, premium, extension_price, tp3):
            await asyncio.sleep(8)
            await send_weakening_alert(bot, chat_id)

            exit_score_ext = calculate_exit_score(fading_premium, premium, extension_price, tp3)
            decision_en_ext, decision_ar_ext = get_exit_decision(exit_score_ext)

            await asyncio.sleep(4)
            await send_exit_score(bot, chat_id, decision_en_ext, decision_ar_ext)


if __name__ == "__main__":
    asyncio.run(main())

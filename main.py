import html
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# =========================================================
# إعدادات Quiet Alpha
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGNAL_CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

RIYADH_TIMEZONE = ZoneInfo("Asia/Riyadh")


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not SIGNAL_CHAT_ID:
    raise ValueError("SIGNAL_CHAT_ID is missing")


# =========================================================
# تسجيل الأحداث والأخطاء
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("quiet-alpha-bot")


# =========================================================
# أدوات مساعدة
# =========================================================

def format_strike(value: str) -> str:
    """التحقق من الاسترايك وترتيبه."""

    strike = float(value)

    if strike <= 0:
        raise ValueError("Strike must be greater than zero")

    if strike.is_integer():
        return str(int(strike))

    return f"{strike:g}"


def format_premium(value: str) -> str:
    """التحقق من سعر العقد وعرضه بمنزلتين عشريتين."""

    premium = float(value)

    if premium <= 0:
        raise ValueError("Premium must be greater than zero")

    return f"{premium:.2f}"


def get_today() -> str:
    """تاريخ اليوم حسب توقيت السعودية."""

    now = datetime.now(RIYADH_TIMEZONE)
    return now.strftime("%d %b %Y")


async def send_signal_to_channel(
    context: ContextTypes.DEFAULT_TYPE,
    signal_type: str,
    strike: str,
    premium: str,
) -> None:
    """إرسال الصفقة إلى قناة Quiet Alpha."""

    if signal_type == "CALL":
        signal_icon = "🟢"
        contract_suffix = "C"
    else:
        signal_icon = "🔴"
        contract_suffix = "P"

    safe_strike = html.escape(strike)
    safe_premium = html.escape(premium)

    message = (
        "🦋 <b>QUIET ALPHA</b>\n\n"
        f"{signal_icon} <b>{signal_type}</b>\n\n"
        f"📍 <b>ENTRY:</b> {safe_strike}{contract_suffix}\n"
        f"💰 <b>PREMIUM:</b> ${safe_premium}\n"
        f"📅 <b>DATE:</b> {get_today()}\n"
        "🎯 <b>CONTRACT GOAL:</b> $100\n\n"
        "<i>Precision. Discipline. Consistency.</i>\n\n"
        "هذه ليست توصية استثمارية أو دعوة للشراء أو البيع."
    )

    await context.bot.send_message(
        chat_id=SIGNAL_CHAT_ID,
        text=message,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# أمر /start
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    message = (
        "🦋 <b>Quiet Alpha Bot</b>\n\n"
        "البوت جاهز لاستقبال الصفقات.\n\n"
        "🟢 لإرسال CALL:\n"
        "<code>/c 6210 4.20</code>\n\n"
        "🔴 لإرسال PUT:\n"
        "<code>/p 6210 3.80</code>\n\n"
        "الرقم الأول: الاسترايك\n"
        "الرقم الثاني: سعر العقد"
    )

    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# أمر CALL
# /c 6210 4.20
# =========================================================

async def call_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام غير صحيح.\n\n"
            "اكتبي مثلًا:\n"
            "/c 6210 4.20"
        )
        return

    try:
        strike = format_strike(context.args[0])
        premium = format_premium(context.args[1])

        await send_signal_to_channel(
            context=context,
            signal_type="CALL",
            strike=strike,
            premium=premium,
        )

        await update.message.reply_text(
            "✅ تم نشر صفقة CALL في قناة Quiet Alpha."
        )

    except ValueError:
        await update.message.reply_text(
            "❌ الاسترايك وسعر العقد يجب أن يكونا أرقامًا.\n\n"
            "مثال:\n"
            "/c 6210 4.20"
        )

    except Exception:
        logger.exception("Failed to publish CALL signal")

        await update.message.reply_text(
            "❌ تعذر نشر صفقة CALL.\n"
            "راجعي BOT_TOKEN وSIGNAL_CHAT_ID وصلاحيات البوت."
        )


# =========================================================
# أمر PUT
# /p 6210 3.80
# =========================================================

async def put_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام غير صحيح.\n\n"
            "اكتبي مثلًا:\n"
            "/p 6210 3.80"
        )
        return

    try:
        strike = format_strike(context.args[0])
        premium = format_premium(context.args[1])

        await send_signal_to_channel(
            context=context,
            signal_type="PUT",
            strike=strike,
            premium=premium,
        )

        await update.message.reply_text(
            "✅ تم نشر صفقة PUT في قناة Quiet Alpha."
        )

    except ValueError:
        await update.message.reply_text(
            "❌ الاسترايك وسعر العقد يجب أن يكونا أرقامًا.\n\n"
            "مثال:\n"
            "/p 6210 3.80"
        )

    except Exception:
        logger.exception("Failed to publish PUT signal")

        await update.message.reply_text(
            "❌ تعذر نشر صفقة PUT.\n"
            "راجعي BOT_TOKEN وSIGNAL_CHAT_ID وصلاحيات البوت."
        )


# =========================================================
# أمر حالة البوت
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    await update.message.reply_text(
        "🟢 Quiet Alpha Bot is online."
    )


# =========================================================
# معالجة الأخطاء
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.error(
        "An error occurred while processing an update",
        exc_info=context.error,
    )


# =========================================================
# تشغيل البوت
# =========================================================

def main() -> None:

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler(["c", "call"], call_command)
    )

    application.add_handler(
        CommandHandler(["p", "put"], put_command)
    )

    application.add_handler(
        CommandHandler("status", status_command)
    )

    application.add_error_handler(error_handler)

    logger.info("Quiet Alpha Bot started successfully")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

import logging
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================================================
# إعدادات Quiet Alpha
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SIGNAL_CHAT_ID = os.getenv("SIGNAL_CHAT_ID")

RIYADH_TIMEZONE = ZoneInfo("Asia/Riyadh")
BASE_DIR = Path(__file__).resolve().parent

CALL_TEMPLATE = BASE_DIR / "call_card.jpg"
PUT_TEMPLATE = BASE_DIR / "put_card.jpg"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not SIGNAL_CHAT_ID:
    raise ValueError("SIGNAL_CHAT_ID is missing")


# =========================================================
# السجلات
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("quiet-alpha-bot")


# =========================================================
# تنسيق البيانات
# =========================================================

def format_strike(value: str) -> str:
    strike = float(value)

    if strike <= 0:
        raise ValueError("Strike must be greater than zero")

    if strike.is_integer():
        return str(int(strike))

    return f"{strike:g}"


def format_premium(value: str) -> str:
    premium = float(value)

    if premium <= 0:
        raise ValueError("Premium must be greater than zero")

    return f"{premium:.2f}"


def get_today() -> str:
    now = datetime.now(RIYADH_TIMEZONE)
    return now.strftime("%d %b %Y")


# =========================================================
# الخطوط
# =========================================================

def load_font(size: int) -> ImageFont.ImageFont:
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]

    for font_path in font_paths:
        if os.path.exists(font_path):
            return ImageFont.truetype(
                font_path,
                size=size,
            )

    logger.warning("No TrueType font found. Using default font.")
    return ImageFont.load_default()


# =========================================================
# كتابة النص داخل البطاقة
# =========================================================

def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    stroke_width: int,
) -> None:

    draw.text(
        position,
        text,
        font=font,
        fill=(255, 255, 255),
        anchor="mm",
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0),
    )


# =========================================================
# إنشاء بطاقة Quiet Alpha
# =========================================================

def create_signal_card(
    signal_type: str,
    strike: str,
    premium: str,
) -> BytesIO:

    if signal_type == "CALL":
        template_path = CALL_TEMPLATE
        contract = f"{strike}C"

    elif signal_type == "PUT":
        template_path = PUT_TEMPLATE
        contract = f"{strike}P"

    else:
        raise ValueError("Unsupported signal type")

    if not template_path.exists():
        raise FileNotFoundError(
            f"Template image was not found: {template_path.name}"
        )

    with Image.open(template_path) as template:
        image = template.convert("RGB")

    draw = ImageDraw.Draw(image)

    width, height = image.size

    # خط العقد والسعر كبير وواضح
    value_font = load_font(
        max(105, int(width * 0.090))
    )

    # التاريخ أصغر قليلًا حتى يناسب الخانة
    date_font = load_font(
        max(70, int(width * 0.058))
    )

    stroke_width = max(
        2,
        int(width * 0.0025),
    )

    # =====================================================
    # مواقع النصوص داخل البطاقة
    # =====================================================

    entry_position = (
        int(width * 0.325),
        int(height * 0.405),
    )

    date_position = (
        int(width * 0.775),
        int(height * 0.405),
    )

    premium_position = (
        int(width * 0.325),
        int(height * 0.515),
    )

    draw_centered_text(
        draw=draw,
        position=entry_position,
        text=contract,
        font=value_font,
        stroke_width=stroke_width,
    )

    draw_centered_text(
        draw=draw,
        position=premium_position,
        text=f"${premium}",
        font=value_font,
        stroke_width=stroke_width,
    )

    draw_centered_text(
        draw=draw,
        position=date_position,
        text=get_today(),
        font=date_font,
        stroke_width=stroke_width,
    )

    output = BytesIO()
    output.name = f"quiet_alpha_{signal_type.lower()}_card.jpg"

    image.save(
        output,
        format="JPEG",
        quality=96,
        optimize=True,
    )

    output.seek(0)
    image.close()

    return output


# =========================================================
# إرسال البطاقة إلى القناة
# =========================================================

async def publish_signal(
    context: ContextTypes.DEFAULT_TYPE,
    signal_type: str,
    strike: str,
    premium: str,
) -> None:

    card = create_signal_card(
        signal_type=signal_type,
        strike=strike,
        premium=premium,
    )

    try:
        await context.bot.send_photo(
            chat_id=SIGNAL_CHAT_ID,
            photo=card,
            connect_timeout=30,
            read_timeout=90,
            write_timeout=90,
            pool_timeout=30,
        )

    finally:
        card.close()


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
        "البوت جاهز لإنشاء البطاقات.\n\n"
        "🟢 CALL:\n"
        "<code>/c 7555 3.90</code>\n\n"
        "🔴 PUT:\n"
        "<code>/p 7555 3.90</code>\n\n"
        "الرقم الأول: الاسترايك\n"
        "الرقم الثاني: سعر العقد"
    )

    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# أمر CALL
# =========================================================

async def call_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n"
            "/c 7555 3.90"
        )
        return

    try:
        strike = format_strike(context.args[0])
        premium = format_premium(context.args[1])

        await publish_signal(
            context=context,
            signal_type="CALL",
            strike=strike,
            premium=premium,
        )

        await update.message.reply_text(
            "✅ تم إنشاء ونشر بطاقة CALL."
        )

    except ValueError:
        await update.message.reply_text(
            "❌ الاسترايك والبريميوم يجب أن يكونا أرقامًا.\n"
            "مثال: /c 7555 3.90"
        )

    except FileNotFoundError:
        logger.exception("CALL template was not found")

        await update.message.reply_text(
            "❌ لم أجد ملف call_card.jpg داخل المشروع."
        )

    except Exception as error:
        logger.exception("Failed to publish CALL card")

        await update.message.reply_text(
            "❌ تعذر إنشاء بطاقة CALL.\n"
            f"نوع الخطأ: {type(error).__name__}"
        )


# =========================================================
# أمر PUT
# =========================================================

async def put_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n"
            "/p 7555 3.90"
        )
        return

    try:
        strike = format_strike(context.args[0])
        premium = format_premium(context.args[1])

        await publish_signal(
            context=context,
            signal_type="PUT",
            strike=strike,
            premium=premium,
        )

        await update.message.reply_text(
            "✅ تم إنشاء ونشر بطاقة PUT."
        )

    except ValueError:
        await update.message.reply_text(
            "❌ الاسترايك والبريميوم يجب أن يكونا أرقامًا.\n"
            "مثال: /p 7555 3.90"
        )

    except FileNotFoundError:
        logger.exception("PUT template was not found")

        await update.message.reply_text(
            "❌ لم أجد ملف put_card.jpg داخل المشروع."
        )

    except Exception as error:
        logger.exception("Failed to publish PUT card")

        await update.message.reply_text(
            "❌ تعذر إنشاء بطاقة PUT.\n"
            f"نوع الخطأ: {type(error).__name__}"
        )


# =========================================================
# حالة البوت
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    call_exists = CALL_TEMPLATE.exists()
    put_exists = PUT_TEMPLATE.exists()

    if call_exists and put_exists:
        message = (
            "🟢 البوت يعمل.\n"
            "✅ قالب CALL موجود.\n"
            "✅ قالب PUT موجود."
        )

    else:
        missing_templates = []

        if not call_exists:
            missing_templates.append("call_card.jpg")

        if not put_exists:
            missing_templates.append("put_card.jpg")

        message = (
            "🟠 البوت يعمل، لكن القوالب التالية غير موجودة:\n"
            + "\n".join(missing_templates)
        )

    await update.message.reply_text(message)


# =========================================================
# معالجة الأخطاء
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.error(
        "Telegram update caused an error",
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

    logger.info(
        "Quiet Alpha Card Bot started successfully"
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()

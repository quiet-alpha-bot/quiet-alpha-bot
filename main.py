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

def load_font(size: int) -> ImageFont.FreeTypeFont:
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]

    for font_path in font_paths:
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size=size)

    return ImageFont.load_default()


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
    else:
        template_path = PUT_TEMPLATE
        contract = f"{strike}P"

    if not template_path.exists():
        raise FileNotFoundError(
            f"Template image was not found: {template_path.name}"
        )

    image = Image.open(template_path).convert("RGB")
    draw = ImageDraw.Draw(image)

    width, height = image.size

    value_font = load_font(max(38, int(width * 0.037)))
    date_font = load_font(max(32, int(width * 0.030)))

    text_color = (245, 245, 245)
    stroke_color = (10, 10, 10)

    # الإحداثيات محسوبة بنسبة حجم الصورة حتى تعمل مع القالبين
    entry_position = (
        int(width * 0.325),
        int(height * 0.418),
    )

    date_position = (
        int(width * 0.790),
        int(height * 0.418),
    )

    premium_position = (
        int(width * 0.325),
        int(height * 0.522),
    )

    draw.text(
        entry_position,
        contract,
        font=value_font,
        fill=text_color,
        anchor="mm",
        stroke_width=2,
        stroke_fill=stroke_color,
    )

    draw.text(
        premium_position,
        f"${premium}",
        font=value_font,
        fill=text_color,
        anchor="mm",
        stroke_width=2,
        stroke_fill=stroke_color,
    )

    draw.text(
        date_position,
        get_today(),
        font=date_font,
        fill=text_color,
        anchor="mm",
        stroke_width=2,
        stroke_fill=stroke_color,
    )

    output = BytesIO()
    output.name = f"quiet_alpha_{signal_type.lower()}.jpg"

    image.save(
        output,
        format="JPEG",
        quality=95,
        optimize=True,
    )

    output.seek(0)
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

    caption = (
        f"🦋 <b>QUIET ALPHA — {signal_type}</b>\n\n"
        f"📍 <b>Contract:</b> {strike}"
        f"{'C' if signal_type == 'CALL' else 'P'}\n"
        f"💰 <b>Premium:</b> ${premium}\n\n"
        "هذه ليست توصية استثمارية أو دعوة للشراء أو البيع."
    )

    await context.bot.send_photo(
        chat_id=SIGNAL_CHAT_ID,
        photo=card,
        caption=caption,
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

    except Exception as error:
        logger.exception("Failed to publish CALL card")

        await update.message.reply_text(
            f"❌ تعذر إنشاء بطاقة CALL.\n"
            f"الخطأ: {type(error).__name__}"
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

    except Exception as error:
        logger.exception("Failed to publish PUT card")

        await update.message.reply_text(
            f"❌ تعذر إنشاء بطاقة PUT.\n"
            f"الخطأ: {type(error).__name__}"
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

    templates_status = (
        CALL_TEMPLATE.exists()
        and PUT_TEMPLATE.exists()
    )

    if templates_status:
        await update.message.reply_text(
            "🟢 البوت يعمل وقوالب CALL وPUT موجودة."
        )
    else:
        await update.message.reply_text(
            "🟠 البوت يعمل، لكن أحد قوالب الصور غير موجود."
        )


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

    logger.info("Quiet Alpha Card Bot started successfully")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

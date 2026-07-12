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
# إعدادات خانات البطاقة
# القيم عبارة عن نسب من عرض وارتفاع الصورة
# =========================================================

ENTRY_BOX = (0.19, 0.365, 0.48, 0.445)
DATE_BOX = (0.63, 0.365, 0.94, 0.445)
PREMIUM_BOX = (0.19, 0.470, 0.48, 0.550)

ENTRY_MAX_FONT_RATIO = 0.105
PREMIUM_MAX_FONT_RATIO = 0.105
DATE_MAX_FONT_RATIO = 0.065

MIN_FONT_RATIO = 0.032

TEXT_COLOR = (255, 255, 255)
STROKE_COLOR = (5, 5, 5)


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

def get_font_path() -> str:
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]

    for font_path in font_paths:
        if os.path.exists(font_path):
            return font_path

    raise FileNotFoundError(
        "No supported TrueType font was found."
    )


FONT_PATH = get_font_path()


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        FONT_PATH,
        size=max(1, int(size)),
    )


# =========================================================
# تحويل الخانات من نسب إلى بكسلات
# =========================================================

def scale_box(
    box: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:

    left, top, right, bottom = box

    return (
        int(left * width),
        int(top * height),
        int(right * width),
        int(bottom * height),
    )


# =========================================================
# اختيار أكبر خط يناسب الخانة
# =========================================================

def fit_font_to_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    card_width: int,
    maximum_font_ratio: float,
) -> ImageFont.FreeTypeFont:

    left, top, right, bottom = box

    box_width = right - left
    box_height = bottom - top

    horizontal_padding = int(box_width * 0.05)
    vertical_padding = int(box_height * 0.05)

    available_width = max(
        1,
        box_width - (horizontal_padding * 2),
    )

    available_height = max(
        1,
        box_height - (vertical_padding * 2),
    )

    maximum_size = max(
        1,
        int(card_width * maximum_font_ratio),
    )

    minimum_size = max(
        1,
        int(card_width * MIN_FONT_RATIO),
    )

    stroke_width = max(
        1,
        int(card_width * 0.0025),
    )

    for font_size in range(
        maximum_size,
        minimum_size - 1,
        -2,
    ):
        font = load_font(font_size)

        bounds = draw.textbbox(
            (0, 0),
            text,
            font=font,
            stroke_width=stroke_width,
        )

        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]

        if (
            text_width <= available_width
            and text_height <= available_height
        ):
            return font

    return load_font(minimum_size)


# =========================================================
# كتابة النص في منتصف الخانة
# =========================================================

def draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    card_width: int,
    maximum_font_ratio: float,
) -> None:

    left, top, right, bottom = box

    center_x = (left + right) // 2
    center_y = (top + bottom) // 2

    font = fit_font_to_box(
        draw=draw,
        text=text,
        box=box,
        card_width=card_width,
        maximum_font_ratio=maximum_font_ratio,
    )

    stroke_width = max(
        1,
        int(card_width * 0.0025),
    )

    draw.text(
        (center_x, center_y),
        text,
        font=font,
        fill=TEXT_COLOR,
        anchor="mm",
        stroke_width=stroke_width,
        stroke_fill=STROKE_COLOR,
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
            f"Template image was not found: "
            f"{template_path.name}"
        )

    with Image.open(template_path) as template:
        image = template.convert("RGB")

    draw = ImageDraw.Draw(image)

    width, height = image.size

    entry_box = scale_box(
        ENTRY_BOX,
        width,
        height,
    )

    premium_box = scale_box(
        PREMIUM_BOX,
        width,
        height,
    )

    date_box = scale_box(
        DATE_BOX,
        width,
        height,
    )

    draw_text_in_box(
        draw=draw,
        text=contract,
        box=entry_box,
        card_width=width,
        maximum_font_ratio=ENTRY_MAX_FONT_RATIO,
    )

    draw_text_in_box(
        draw=draw,
        text=f"${premium}",
        box=premium_box,
        card_width=width,
        maximum_font_ratio=PREMIUM_MAX_FONT_RATIO,
    )

    draw_text_in_box(
        draw=draw,
        text=get_today(),
        box=date_box,
        card_width=width,
        maximum_font_ratio=DATE_MAX_FONT_RATIO,
    )

    output = BytesIO()
    output.name = (
        f"quiet_alpha_"
        f"{signal_type.lower()}_card.jpg"
    )

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
        "البوت جاهز لإنشاء بطاقات الصفقات.\n\n"
        "🟢 لإرسال CALL:\n"
        "<code>/c 7555 3.90</code>\n\n"
        "🔴 لإرسال PUT:\n"
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
            "❌ الاستخدام الصحيح:\n\n"
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

    except (ValueError, TypeError):
        await update.message.reply_text(
            "❌ الاسترايك وسعر العقد يجب أن "
            "يكونا أرقامًا صحيحة.\n\n"
            "مثال:\n"
            "/c 7555 3.90"
        )

    except FileNotFoundError as error:
        logger.exception(
            "CALL template or font was not found"
        )

        await update.message.reply_text(
            "❌ ملف قالب CALL أو الخط غير موجود.\n"
            f"{error}"
        )

    except Exception as error:
        logger.exception(
            "Failed to publish CALL card"
        )

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
            "❌ الاستخدام الصحيح:\n\n"
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

    except (ValueError, TypeError):
        await update.message.reply_text(
            "❌ الاسترايك وسعر العقد يجب أن "
            "يكونا أرقامًا صحيحة.\n\n"
            "مثال:\n"
            "/p 7555 3.90"
        )

    except FileNotFoundError as error:
        logger.exception(
            "PUT template or font was not found"
        )

        await update.message.reply_text(
            "❌ ملف قالب PUT أو الخط غير موجود.\n"
            f"{error}"
        )

    except Exception as error:
        logger.exception(
            "Failed to publish PUT card"
        )

        await update.message.reply_text(
            "❌ تعذر إنشاء بطاقة PUT.\n"
            f"نوع الخطأ: {type(error).__name__}"
        )


# =========================================================
# أمر /status
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
            missing_templates.append(
                "call_card.jpg"
            )

        if not put_exists:
            missing_templates.append(
                "put_card.jpg"
            )

        message = (
            "🟠 البوت يعمل، لكن القوالب "
            "التالية غير موجودة:\n"
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
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            ["c", "call"],
            call_command,
        )
    )

    application.add_handler(
        CommandHandler(
            ["p", "put"],
            put_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Quiet Alpha Card Bot started successfully"
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()

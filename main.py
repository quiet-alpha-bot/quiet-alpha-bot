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
OWNER_USER_ID_RAW = os.getenv("OWNER_USER_ID")

RIYADH_TIMEZONE = ZoneInfo("Asia/Riyadh")
BASE_DIR = Path(__file__).resolve().parent

CALL_TEMPLATE = BASE_DIR / "call_card.jpg"
PUT_TEMPLATE = BASE_DIR / "put_card.jpg"
SUCCESS_TEMPLATE = BASE_DIR / "success_card.jpg"
HIGHEST_PRICE_TEMPLATE = BASE_DIR / "highest_price_card.jpg"
STOP_LOSS_TEMPLATE = BASE_DIR / "STOP_LOSS_CARD_50.jpg"

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

# httpx logs every request URL at INFO level; Telegram URLs contain the
# bot token, so keep that logger at WARNING to never print the token.
logging.getLogger("httpx").setLevel(logging.WARNING)


# =========================================================
# صلاحية المالك (OWNER_USER_ID)
# =========================================================

def parse_owner_user_id(raw_value):
    """Telegram user ids are positive integers; anything else is invalid."""
    if raw_value is None:
        return None

    raw_value = raw_value.strip()

    if not raw_value.isdigit():
        return None

    value = int(raw_value)

    return value if value > 0 else None


OWNER_USER_ID = parse_owner_user_id(OWNER_USER_ID_RAW)

if OWNER_USER_ID is None:
    logger.error(
        "OWNER_USER_ID is missing or invalid. "
        "Protected commands are locked (fail closed)."
    )


async def ensure_owner(update: Update) -> bool:
    """Fail closed: only OWNER_USER_ID may run protected commands."""
    user = update.effective_user

    if (
        OWNER_USER_ID is not None
        and user is not None
        and user.id == OWNER_USER_ID
    ):
        return True

    logger.warning(
        "Unauthorized command attempt from user %s",
        getattr(user, "id", None),
    )

    if update.message is not None:
        await update.message.reply_text(
            "⛔ غير مصرح."
        )

    return False


# =========================================================
# خانات بطاقات CALL وPUT
# =========================================================

ENTRY_BOX = (
    0.19,
    0.365,
    0.48,
    0.445,
)

DATE_BOX = (
    0.63,
    0.365,
    0.94,
    0.445,
)

PREMIUM_BOX = (
    0.19,
    0.470,
    0.48,
    0.550,
)


# =========================================================
# خانات بطاقة الصفقة الناجحة
# success_card.jpg = 1536 × 1024
# =========================================================

SUCCESS_ENTRY_BOX = (
    0.08,
    0.655,
    0.33,
    0.785,
)

SUCCESS_EXIT_BOX = (
    0.36,
    0.655,
    0.63,
    0.785,
)

SUCCESS_DATE_BOX = (
    0.22,
    0.855,
    0.48,
    0.955,
)


# =========================================================
# خانات بطاقة أعلى سعر
# highest_price_card.jpg = 1536 × 1024
# =========================================================

HIGHEST_ENTRY_BOX = (
    0.055,
    0.755,
    0.250,
    0.855,
)

HIGHEST_PRICE_BOX = (
    0.275,
    0.755,
    0.475,
    0.855,
)

HIGHEST_RETURN_BOX = (
    0.515,
    0.755,
    0.715,
    0.855,
)

HIGHEST_PROFIT_BOX = (
    0.755,
    0.755,
    0.955,
    0.855,
)

HIGHEST_DATE_BOX = (
    0.105,
    0.895,
    0.340,
    0.975,
)


# =========================================================
# خانات بطاقة وقف الخسارة
# STOP_LOSS_CARD_50.jpg = 1536 × 1024
# =========================================================

STOP_ENTRY_BOX = (
    0.055,
    0.680,
    0.315,
    0.825,
)

STOP_LOSS_BOX = (
    0.350,
    0.680,
    0.635,
    0.825,
)

STOP_DATE_BOX = (
    0.675,
    0.680,
    0.945,
    0.825,
)


# =========================================================
# أحجام الخط
# =========================================================

ENTRY_MAX_FONT_RATIO = 0.046
PREMIUM_MAX_FONT_RATIO = 0.046
DATE_MAX_FONT_RATIO = 0.046

SUCCESS_PRICE_FONT_RATIO = 0.060
SUCCESS_DATE_FONT_RATIO = 0.040

HIGHEST_VALUE_FONT_RATIO = 0.050
HIGHEST_DATE_FONT_RATIO = 0.036

STOP_VALUE_FONT_RATIO = 0.060
STOP_DATE_FONT_RATIO = 0.043

MIN_FONT_RATIO = 0.022


# =========================================================
# إعدادات وقف الخسارة
# =========================================================

STOP_LOSS_PERCENTAGE = 0.50


# =========================================================
# ألوان النص
# =========================================================

GOLD_TEXT_COLOR = (218, 165, 75)
WHITE_TEXT_COLOR = (242, 239, 230)
GREEN_TEXT_COLOR = (121, 211, 55)
RED_TEXT_COLOR = (238, 42, 42)

STROKE_COLOR = (10, 7, 3)

# سماكة الحدود حول النص
STROKE_WIDTH_RATIO = 0.0022

# سماكة إضافية للنص نفسه
BOLD_OFFSET_RATIO = 0.0013


# =========================================================
# تنسيق البيانات
# =========================================================

def format_strike(value: str) -> str:
    strike = float(value)

    if strike <= 0:
        raise ValueError(
            "Strike must be greater than zero"
        )

    if strike.is_integer():
        return str(int(strike))

    return f"{strike:g}"


def format_premium(value: str) -> str:
    premium = float(value)

    if premium <= 0:
        raise ValueError(
            "Premium must be greater than zero"
        )

    return f"{premium:.2f}"


def format_percentage(value: float) -> str:
    rounded_value = round(value, 1)

    if rounded_value.is_integer():
        return f"+{int(rounded_value)}%"

    return f"+{rounded_value:.1f}%"


def format_profit(value: float) -> str:
    rounded_value = round(value, 2)

    if rounded_value.is_integer():
        return f"+${int(rounded_value):,}"

    return f"+${rounded_value:,.2f}"


def format_loss(value: float) -> str:
    rounded_value = round(abs(value), 2)

    if rounded_value.is_integer():
        return f"-${int(rounded_value):,}"

    return f"-${rounded_value:,.2f}"


def get_today() -> str:
    now = datetime.now(RIYADH_TIMEZONE)

    return now.strftime(
        "%d %b %Y"
    )


# =========================================================
# حساب أعلى سعر
# =========================================================

def calculate_highest_result(
    entry_price: float,
    highest_price: float,
) -> tuple[float, float]:

    if highest_price <= entry_price:
        raise ValueError(
            "Highest price must be greater than entry price"
        )

    return_percentage = (
        (highest_price - entry_price)
        / entry_price
    ) * 100

    highest_profit = (
        highest_price - entry_price
    ) * 100

    return return_percentage, highest_profit


# =========================================================
# حساب وقف الخسارة 50%
# =========================================================

def calculate_stop_loss(
    entry_price: float,
) -> float:

    if entry_price <= 0:
        raise ValueError(
            "Entry price must be greater than zero"
        )

    return (
        entry_price
        * STOP_LOSS_PERCENTAGE
    )


# =========================================================
# تحميل الخط
# =========================================================

def load_font(size: int):
    requested_size = max(
        1,
        int(size),
    )

    font_paths = [
        (
            "/usr/share/fonts/truetype/"
            "dejavu/DejaVuSans-Bold.ttf"
        ),
        (
            "/usr/share/fonts/dejavu/"
            "DejaVuSans-Bold.ttf"
        ),
        (
            "/usr/share/fonts/truetype/"
            "liberation2/LiberationSans-Bold.ttf"
        ),
        (
            "/usr/share/fonts/truetype/"
            "liberation/LiberationSans-Bold.ttf"
        ),
        (
            "/usr/share/fonts/truetype/"
            "freefont/FreeSansBold.ttf"
        ),
        (
            "/usr/share/fonts/truetype/"
            "noto/NotoSans-Bold.ttf"
        ),
        (
            "/System/Library/Fonts/"
            "Supplemental/Arial Bold.ttf"
        ),
    ]

    for font_path in font_paths:
        if not os.path.exists(font_path):
            continue

        try:
            return ImageFont.truetype(
                font_path,
                size=requested_size,
            )

        except Exception:
            logger.exception(
                "Could not load font: %s",
                font_path,
            )

    logger.warning(
        "No TrueType font found. "
        "Using Pillow default font."
    )

    try:
        return ImageFont.load_default(
            size=requested_size
        )

    except TypeError:
        return ImageFont.load_default()


# =========================================================
# تحويل الخانة من نسب إلى بكسلات
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
):
    left, top, right, bottom = box

    box_width = right - left
    box_height = bottom - top

    horizontal_padding = max(
        4,
        int(box_width * 0.07),
    )

    vertical_padding = max(
        2,
        int(box_height * 0.08),
    )

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
        18,
        int(card_width * MIN_FONT_RATIO),
    )

    stroke_width = max(
        1,
        int(card_width * STROKE_WIDTH_RATIO),
    )

    bold_offset = max(
        1,
        int(card_width * BOLD_OFFSET_RATIO),
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

        text_width = (
            bounds[2] - bounds[0]
        ) + (bold_offset * 2)

        text_height = (
            bounds[3] - bounds[1]
        ) + (bold_offset * 2)

        if (
            text_width <= available_width
            and text_height <= available_height
        ):
            return font

    return load_font(minimum_size)


# =========================================================
# كتابة النص داخل الخانة بخط أثقل
# =========================================================

def draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    card_width: int,
    maximum_font_ratio: float,
    text_color: tuple[int, int, int] = GOLD_TEXT_COLOR,
) -> None:

    left, top, right, bottom = box

    center_x = (
        left + right
    ) // 2

    center_y = (
        top + bottom
    ) // 2

    font = fit_font_to_box(
        draw=draw,
        text=text,
        box=box,
        card_width=card_width,
        maximum_font_ratio=maximum_font_ratio,
    )

    stroke_width = max(
        1,
        int(card_width * STROKE_WIDTH_RATIO),
    )

    bold_offset = max(
        1,
        int(card_width * BOLD_OFFSET_RATIO),
    )

    bold_offsets = [
        (-bold_offset, 0),
        (bold_offset, 0),
        (0, -bold_offset),
        (0, bold_offset),
        (-bold_offset, -bold_offset),
        (-bold_offset, bold_offset),
        (bold_offset, -bold_offset),
        (bold_offset, bold_offset),
        (0, 0),
    ]

    for offset_x, offset_y in bold_offsets:
        draw.text(
            (
                center_x + offset_x,
                center_y + offset_y,
            ),
            text,
            font=font,
            fill=text_color,
            anchor="mm",
            stroke_width=stroke_width,
            stroke_fill=STROKE_COLOR,
        )


# =========================================================
# حفظ الصورة داخل الذاكرة
# =========================================================

def save_image_to_buffer(
    image: Image.Image,
    filename: str,
) -> BytesIO:

    output = BytesIO()
    output.name = filename

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
# إنشاء بطاقة CALL أو PUT
# =========================================================

def create_signal_card(
    signal_type: str,
    strike: str,
    premium: str,
) -> BytesIO:

    if signal_type == "CALL":
        template_path = CALL_TEMPLATE
        contract = strike

    elif signal_type == "PUT":
        template_path = PUT_TEMPLATE
        contract = strike

    else:
        raise ValueError(
            "Unsupported signal type"
        )

    if not template_path.exists():
        raise FileNotFoundError(
            f"Template image was not found: "
            f"{template_path.name}"
        )

    with Image.open(template_path) as template:
        image = template.convert("RGB")

    draw = ImageDraw.Draw(image)
    width, height = image.size

    draw_text_in_box(
        draw=draw,
        text=contract,
        box=scale_box(
            ENTRY_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=ENTRY_MAX_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=f"${premium}",
        box=scale_box(
            PREMIUM_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=PREMIUM_MAX_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=get_today(),
        box=scale_box(
            DATE_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=DATE_MAX_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    return save_image_to_buffer(
        image=image,
        filename=(
            f"quiet_alpha_"
            f"{signal_type.lower()}_card.jpg"
        ),
    )


# =========================================================
# إنشاء بطاقة الصفقة الناجحة
# =========================================================

# Versioned templates are shared by automatic and manual result cards.
from automation.result_cards import (
    create_success_card, create_highest_price_card, create_stop_loss_card,
)


# =========================================================
# إرسال صورة إلى القناة
# =========================================================

async def send_card_to_channel(
    context: ContextTypes.DEFAULT_TYPE,
    card: BytesIO,
) -> None:

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
# إرسال بطاقة CALL أو PUT
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

    await send_card_to_channel(
        context=context,
        card=card,
    )


# =========================================================
# إرسال بطاقة الصفقة الناجحة
# =========================================================

async def publish_success_card(
    context: ContextTypes.DEFAULT_TYPE,
    entry_price: str,
    exit_price: str,
    contract_name: str = "",
) -> None:

    card = create_success_card(
        contract_name=contract_name,
        entry_price=entry_price,
        exit_price=exit_price,
    )

    await send_card_to_channel(
        context=context,
        card=card,
    )


# =========================================================
# إرسال بطاقة أعلى سعر
# =========================================================

async def publish_highest_price_card(
    context: ContextTypes.DEFAULT_TYPE,
    entry_price: str,
    highest_price: str,
    contract_name: str = "",
) -> None:

    card = create_highest_price_card(
        contract_name=contract_name,
        entry_price=entry_price,
        highest_price=highest_price,
    )

    await send_card_to_channel(
        context=context,
        card=card,
    )


# =========================================================
# إرسال بطاقة وقف الخسارة
# =========================================================

async def publish_stop_loss_card(
    context: ContextTypes.DEFAULT_TYPE,
    entry_price: str,
    contract_name: str = "",
) -> None:

    card = create_stop_loss_card(
        contract_name=contract_name,
        entry_price=entry_price,
    )

    await send_card_to_channel(
        context=context,
        card=card,
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
        "البوت جاهز لإنشاء بطاقات الصفقات.\n\n"
        "🟢 CALL:\n"
        "<code>/c 7555 3.90</code>\n\n"
        "🔴 PUT:\n"
        "<code>/p 7555 3.90</code>\n\n"
        "✅ صفقة ناجحة:\n"
        "<code>/win 3.90 4.90</code>\n\n"
        "🏆 أعلى سعر:\n"
        "<code>/max 3.90 8.40</code>\n\n"
        "🛡️ وقف خسارة 50%:\n"
        "<code>/stop 3.90</code>\n\n"
        "في أمر /stop اكتبي سعر الدخول فقط."
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

    if not await ensure_owner(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/c 7555 3.90"
        )
        return

    try:
        strike = format_strike(
            context.args[0]
        )

        premium = format_premium(
            context.args[1]
        )

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
            "❌ الاسترايك وسعر العقد "
            "يجب أن يكونا أرقامًا صحيحة."
        )

    except FileNotFoundError as error:
        logger.exception(
            "CALL template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد قالب CALL.\n"
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

    if not await ensure_owner(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/p 7555 3.90"
        )
        return

    try:
        strike = format_strike(
            context.args[0]
        )

        premium = format_premium(
            context.args[1]
        )

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
            "❌ الاسترايك وسعر العقد "
            "يجب أن يكونا أرقامًا صحيحة."
        )

    except FileNotFoundError as error:
        logger.exception(
            "PUT template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد قالب PUT.\n"
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
# أمر /win
# =========================================================

def manual_card_args(args, price_count):
    """Legacy prices, or ROOT STRIKE CALL/PUT followed by prices.

    Identity is for rendering only; this does not register a tracked trade.
    """
    from decimal import Decimal, InvalidOperation
    import re
    if len(args) == price_count:
        price_args, label = args, ""
    elif len(args) == price_count + 3:
        root, raw_strike, direction = args[0].upper(), args[1], args[2].upper()
        if not re.fullmatch(r"[A-Z]{1,6}", root) or direction not in {"CALL", "PUT"}:
            raise ValueError("Invalid contract identity")
        try:
            strike = Decimal(raw_strike)
        except InvalidOperation as exc:
            raise ValueError("Invalid strike") from exc
        if not strike.is_finite() or not 0 < strike < 100000 or strike*1000 != (strike*1000).to_integral_value():
            raise ValueError("Invalid strike")
        strike_label = format(strike, 'f').rstrip('0').rstrip('.') if '.' in format(strike, 'f') else str(strike)
        label = f"{root} · {strike_label} {direction}"
        price_args = args[3:]
    else:
        raise ValueError("Invalid argument count")
    for raw in price_args:
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError("Invalid price") from exc
        if not value.is_finite() or value <= 0:
            raise ValueError("Invalid price")
    return price_args, label


async def win_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if not await ensure_owner(update):
        return

    if len(context.args) not in (2, 5):
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/win SPXW 7770 CALL 3.10 3.60\n\n"
            "الترتيب: الأصل، الاسترايك، CALL أو PUT، سعر الدخول\n"
            "ثم سعر تحقيق الهدف"
        )
        return

    try:
        price_args, contract_name = manual_card_args(context.args, 2)
        entry_price = format_premium(
            price_args[0]
        )

        exit_price = format_premium(
            price_args[1]
        )

        if (
            float(exit_price)
            <= float(entry_price)
        ):
            await update.message.reply_text(
                "❌ السعر المحقق يجب أن يكون "
                "أعلى من سعر الدخول."
            )
            return

        await publish_success_card(
            contract_name=contract_name,
            context=context,
            entry_price=entry_price,
            exit_price=exit_price,
        )

        await update.message.reply_text(
            "✅ تم إنشاء ونشر بطاقة الصفقة الناجحة."
        )

    except (ValueError, TypeError):
        await update.message.reply_text(
            "❌ تأكدي من بيانات العقد والأسعار؛ "
            "الأسعار موجبة والنوع CALL أو PUT."
        )

    except FileNotFoundError as error:
        logger.exception(
            "Success template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف qa_success_v2.png.\n"
            f"{error}"
        )

    except Exception as error:
        logger.exception(
            "Failed to publish success card"
        )

        await update.message.reply_text(
            "❌ تعذر إنشاء بطاقة الصفقة الناجحة.\n"
            f"نوع الخطأ: {type(error).__name__}"
        )


# =========================================================
# أمر /max
# =========================================================

async def max_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if not await ensure_owner(update):
        return

    if len(context.args) not in (2, 5):
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/max SPXW 7770 CALL 3.10 5.20\n\n"
            "الترتيب: الأصل، الاسترايك، CALL أو PUT، سعر الدخول\n"
            "ثم أعلى سعر"
        )
        return

    try:
        price_args, contract_name = manual_card_args(context.args, 2)
        entry_price = format_premium(
            price_args[0]
        )

        highest_price = format_premium(
            price_args[1]
        )

        if (
            float(highest_price)
            <= float(entry_price)
        ):
            await update.message.reply_text(
                "❌ أعلى سعر يجب أن يكون "
                "أكبر من سعر الدخول."
            )
            return

        await publish_highest_price_card(
            contract_name=contract_name,
            context=context,
            entry_price=entry_price,
            highest_price=highest_price,
        )

        return_percentage, highest_profit = (
            calculate_highest_result(
                entry_price=float(entry_price),
                highest_price=float(highest_price),
            )
        )

        await update.message.reply_text(
            "✅ تم إنشاء ونشر بطاقة أعلى سعر.\n\n"
            f"📈 العائد: "
            f"{format_percentage(return_percentage)}\n"
            f"💰 أعلى ربح: "
            f"{format_profit(highest_profit)}"
        )

    except (ValueError, TypeError):
        await update.message.reply_text(
            "❌ تأكدي من بيانات العقد والأسعار؛ "
            "الأسعار موجبة والنوع CALL أو PUT.\n\n"
            "مثال:\n"
            "/max SPXW 7770 CALL 3.10 5.20"
        )

    except FileNotFoundError as error:
        logger.exception(
            "Highest-price template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف "
            "qa_highest_v2.png.\n"
            f"{error}"
        )

    except Exception as error:
        logger.exception(
            "Failed to publish highest-price card"
        )

        await update.message.reply_text(
            "❌ تعذر إنشاء بطاقة أعلى سعر.\n"
            f"نوع الخطأ: {type(error).__name__}"
        )


# =========================================================
# أمر /stop
# /stop SPXW 7700 PUT 4.00
# =========================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if not await ensure_owner(update):
        return

    if len(context.args) not in (1, 4):
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/stop SPXW 7700 PUT 4.00\n\n"
            "الترتيب: الأصل، الاسترايك، CALL أو PUT، سعر الدخول."
        )
        return

    try:
        price_args, contract_name = manual_card_args(context.args, 1)
        entry_price = format_premium(
            price_args[0]
        )

        loss_value = calculate_stop_loss(
            entry_price=float(entry_price),
        )

        await publish_stop_loss_card(
            contract_name=contract_name,
            context=context,
            entry_price=entry_price,
        )

        await update.message.reply_text(
            "✅ تم إنشاء ونشر بطاقة وقف الخسارة.\n\n"
            f"🛡️ الوقف: 50%\n"
            f"🔻 الخسارة: "
            f"{format_loss(loss_value)}"
        )

    except (ValueError, TypeError):
        await update.message.reply_text(
            "❌ تأكدي من الأصل والاسترايك وCALL أو PUT؛ سعر الدخول "
            "يجب أن يكون موجبًا.\n\n"
            "مثال:\n"
            "/stop SPXW 7700 PUT 4.00"
        )

    except FileNotFoundError as error:
        logger.exception(
            "Stop-loss template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف qa_stop_v2.png.\n"
            f"{error}"
        )

    except Exception as error:
        logger.exception(
            "Failed to publish stop-loss card"
        )

        await update.message.reply_text(
            "❌ تعذر إنشاء بطاقة وقف الخسارة.\n"
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

    if not await ensure_owner(update):
        return

    templates = {
        "call_card.jpg": CALL_TEMPLATE.exists(),
        "put_card.jpg": PUT_TEMPLATE.exists(),
        "success_card.jpg": SUCCESS_TEMPLATE.exists(),
        (
            "highest_price_card.jpg"
        ): HIGHEST_PRICE_TEMPLATE.exists(),
        "STOP_LOSS_CARD_50.jpg": STOP_LOSS_TEMPLATE.exists(),
    }

    missing_templates = [
        name
        for name, exists in templates.items()
        if not exists
    ]

    if not missing_templates:
        message = (
            "🟢 البوت يعمل.\n"
            "✅ قالب CALL موجود.\n"
            "✅ قالب PUT موجود.\n"
            "✅ قالب SUCCESS موجود.\n"
            "✅ قالب HIGHEST PRICE موجود.\n"
            "✅ قالب STOP LOSS موجود."
        )

    else:
        message = (
            "🟠 البوت يعمل، لكن القوالب "
            "التالية غير موجودة:\n"
            + "\n".join(missing_templates)
        )

    await update.message.reply_text(
        message
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
# Private Automation (اختياري)
# AUTOMATION_ENABLED=false → البوت اليدوي فقط، تمامًا كما كان
# =========================================================

def automation_enabled() -> bool:
    value = os.getenv("AUTOMATION_ENABLED", "")

    return value.strip().lower() in {"1", "true", "yes", "on"}


def setup_private_automation(application: Application) -> None:

    if not automation_enabled():
        logger.info("Automation disabled (AUTOMATION_ENABLED is not true)")
        return

    try:
        from automation.cards import CardRenderers
        from automation.runtime import setup_automation

        renderers = CardRenderers(
            create_signal_card=create_signal_card,
            create_success_card=create_success_card,
            create_highest_price_card=create_highest_price_card,
            create_stop_loss_card=create_stop_loss_card,
            calculate_highest_result=calculate_highest_result,
            format_strike=format_strike,
            format_premium=format_premium,
        )

        setup_automation(application, renderers)

    except Exception:
        logger.exception(
            "Automation setup failed. Manual bot continues normally."
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
            ["win", "success"],
            win_command,
        )
    )

    application.add_handler(
        CommandHandler(
            ["max", "highest"],
            max_command,
        )
    )

    application.add_handler(
        CommandHandler(
            ["stop", "loss"],
            stop_command,
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

    setup_private_automation(application)

    logger.info(
        "Quiet Alpha Card Bot started successfully"
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()

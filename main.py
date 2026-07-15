import logging
import os
from datetime import datetime, timedelta
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
SUCCESS_TEMPLATE = BASE_DIR / "success_card.jpg"
HIGHEST_PRICE_TEMPLATE = BASE_DIR / "highest_price_card.jpg"
STOP_LOSS_TEMPLATE = BASE_DIR / "Stop-Loss.jpg"
WEEKLY_TEMPLATE = BASE_DIR / "weekly_results_card.jpg"
MONTHLY_TEMPLATE = BASE_DIR / "monthly_results_card.jpg"

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
# Stop-Loss.jpg = 1080 × 720
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
# خانات بطاقتي الأداء الأسبوعي والشهري
#
# Weekly  = 720 × 971 تقريبًا
# Monthly = 720 × 967 تقريبًا
# =========================================================

PERFORMANCE_TOTAL_TRADES_BOX = (
    0.035,
    0.355,
    0.280,
    0.455,
)

PERFORMANCE_WINNING_TRADES_BOX = (
    0.290,
    0.355,
    0.500,
    0.455,
)

PERFORMANCE_LOSING_TRADES_BOX = (
    0.505,
    0.355,
    0.720,
    0.455,
)

PERFORMANCE_WIN_RATE_BOX = (
    0.725,
    0.355,
    0.965,
    0.455,
)

PERFORMANCE_GROSS_PROFIT_BOX = (
    0.035,
    0.525,
    0.280,
    0.625,
)

PERFORMANCE_GROSS_LOSS_BOX = (
    0.290,
    0.525,
    0.500,
    0.625,
)

PERFORMANCE_NET_PROFIT_BOX = (
    0.505,
    0.525,
    0.720,
    0.625,
)

PERFORMANCE_SAR_PROFIT_BOX = (
    0.725,
    0.525,
    0.965,
    0.625,
)

PERFORMANCE_BEST_TRADE_BOX = (
    0.120,
    0.695,
    0.485,
    0.785,
)

PERFORMANCE_WORST_TRADE_BOX = (
    0.560,
    0.695,
    0.940,
    0.785,
)

PERFORMANCE_DATE_RANGE_BOX = (
    0.255,
    0.915,
    0.750,
    0.970,
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

PERFORMANCE_NUMBER_FONT_RATIO = 0.052
PERFORMANCE_MONEY_FONT_RATIO = 0.037
PERFORMANCE_TRADE_FONT_RATIO = 0.037
PERFORMANCE_DATE_FONT_RATIO = 0.028

MIN_FONT_RATIO = 0.022


# =========================================================
# إعدادات الحساب
# =========================================================

STOP_LOSS_PERCENTAGE = 0.50
USD_TO_SAR = 3.75


# =========================================================
# ألوان النص
# =========================================================

GOLD_TEXT_COLOR = (218, 165, 75)
WHITE_TEXT_COLOR = (242, 239, 230)
GREEN_TEXT_COLOR = (121, 211, 55)
RED_TEXT_COLOR = (238, 42, 42)

STROKE_COLOR = (10, 7, 3)
STROKE_WIDTH_RATIO = 0.0015


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


def format_win_rate(value: float) -> str:
    rounded_value = round(value, 1)

    if rounded_value.is_integer():
        return f"{int(rounded_value)}%"

    return f"{rounded_value:.1f}%"


def format_profit(value: float) -> str:
    rounded_value = round(abs(value), 2)

    if rounded_value.is_integer():
        return f"+${int(rounded_value):,}"

    return f"+${rounded_value:,.2f}"


def format_loss(value: float) -> str:
    rounded_value = round(abs(value), 2)

    if rounded_value.is_integer():
        return f"-${int(rounded_value):,}"

    return f"-${rounded_value:,.2f}"


def format_sar(
    value: float,
    include_sign: bool = False,
) -> str:
    rounded_value = round(abs(value), 2)

    if rounded_value.is_integer():
        value_text = f"{int(rounded_value):,} SAR"
    else:
        value_text = f"{rounded_value:,.2f} SAR"

    if not include_sign:
        return value_text

    if value < 0:
        return f"-{value_text}"

    return value_text


def get_today() -> str:
    now = datetime.now(RIYADH_TIMEZONE)

    return now.strftime(
        "%d %b %Y"
    )


def get_week_range() -> str:
    today = datetime.now(RIYADH_TIMEZONE)

    week_start = today - timedelta(
        days=today.weekday()
    )

    week_end = week_start + timedelta(
        days=6
    )

    return (
        f"{week_start.strftime('%d %b %Y')}"
        f" - "
        f"{week_end.strftime('%d %b %Y')}"
    )


def get_month_range() -> str:
    now = datetime.now(RIYADH_TIMEZONE)

    month_start = now.replace(
        day=1
    )

    if now.month == 12:
        next_month = now.replace(
            year=now.year + 1,
            month=1,
            day=1,
        )
    else:
        next_month = now.replace(
            month=now.month + 1,
            day=1,
        )

    month_end = (
        next_month
        - timedelta(days=1)
    )

    return (
        f"{month_start.strftime('%d %b %Y')}"
        f" - "
        f"{month_end.strftime('%d %b %Y')}"
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
        int(box_width * 0.06),
    )

    vertical_padding = max(
        2,
        int(box_height * 0.06),
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
        16,
        int(card_width * MIN_FONT_RATIO),
    )

    stroke_width = max(
        1,
        int(card_width * STROKE_WIDTH_RATIO),
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
        )

        text_height = (
            bounds[3] - bounds[1]
        )

        if (
            text_width <= available_width
            and text_height <= available_height
        ):
            return font

    return load_font(minimum_size)


# =========================================================
# كتابة النص داخل الخانة
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

    draw.text(
        (center_x, center_y),
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

def create_success_card(
    entry_price: str,
    exit_price: str,
) -> BytesIO:

    if not SUCCESS_TEMPLATE.exists():
        raise FileNotFoundError(
            "Template image was not found: "
            "success_card.jpg"
        )

    with Image.open(SUCCESS_TEMPLATE) as template:
        image = template.convert("RGB")

    draw = ImageDraw.Draw(image)
    width, height = image.size

    draw_text_in_box(
        draw=draw,
        text=f"${entry_price}",
        box=scale_box(
            SUCCESS_ENTRY_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=SUCCESS_PRICE_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=f"${exit_price}",
        box=scale_box(
            SUCCESS_EXIT_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=SUCCESS_PRICE_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=get_today(),
        box=scale_box(
            SUCCESS_DATE_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=SUCCESS_DATE_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    return save_image_to_buffer(
        image=image,
        filename="quiet_alpha_success_card.jpg",
    )


# =========================================================
# إنشاء بطاقة أعلى سعر
# =========================================================

def create_highest_price_card(
    entry_price: str,
    highest_price: str,
) -> BytesIO:

    if not HIGHEST_PRICE_TEMPLATE.exists():
        raise FileNotFoundError(
            "Template image was not found: "
            "highest_price_card.jpg"
        )

    entry_value = float(entry_price)
    highest_value = float(highest_price)

    return_percentage, highest_profit = (
        calculate_highest_result(
            entry_price=entry_value,
            highest_price=highest_value,
        )
    )

    with Image.open(
        HIGHEST_PRICE_TEMPLATE
    ) as template:
        image = template.convert("RGB")

    draw = ImageDraw.Draw(image)
    width, height = image.size

    draw_text_in_box(
        draw=draw,
        text=f"${entry_price}",
        box=scale_box(
            HIGHEST_ENTRY_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=HIGHEST_VALUE_FONT_RATIO,
        text_color=WHITE_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=f"${highest_price}",
        box=scale_box(
            HIGHEST_PRICE_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=HIGHEST_VALUE_FONT_RATIO,
        text_color=WHITE_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=format_percentage(
            return_percentage
        ),
        box=scale_box(
            HIGHEST_RETURN_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=HIGHEST_VALUE_FONT_RATIO,
        text_color=GREEN_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=format_profit(
            highest_profit
        ),
        box=scale_box(
            HIGHEST_PROFIT_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=HIGHEST_VALUE_FONT_RATIO,
        text_color=GREEN_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=get_today(),
        box=scale_box(
            HIGHEST_DATE_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=HIGHEST_DATE_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    return save_image_to_buffer(
        image=image,
        filename=(
            "quiet_alpha_highest_price_card.jpg"
        ),
    )


# =========================================================
# إنشاء بطاقة وقف الخسارة
# =========================================================

def create_stop_loss_card(
    entry_price: str,
) -> BytesIO:

    if not STOP_LOSS_TEMPLATE.exists():
        raise FileNotFoundError(
            "Template image was not found: "
            "Stop-Loss.jpg"
        )

    entry_value = float(entry_price)

    loss_value = calculate_stop_loss(
        entry_price=entry_value,
    )

    with Image.open(
        STOP_LOSS_TEMPLATE
    ) as template:
        image = template.convert("RGB")

    draw = ImageDraw.Draw(image)
    width, height = image.size

    draw_text_in_box(
        draw=draw,
        text=f"${entry_price}",
        box=scale_box(
            STOP_ENTRY_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=STOP_VALUE_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=format_loss(loss_value),
        box=scale_box(
            STOP_LOSS_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=STOP_VALUE_FONT_RATIO,
        text_color=RED_TEXT_COLOR,
    )

    draw_text_in_box(
        draw=draw,
        text=get_today(),
        box=scale_box(
            STOP_DATE_BOX,
            width,
            height,
        ),
        card_width=width,
        maximum_font_ratio=STOP_DATE_FONT_RATIO,
        text_color=GOLD_TEXT_COLOR,
    )

    return save_image_to_buffer(
        image=image,
        filename=(
            "quiet_alpha_stop_loss_card.jpg"
        ),
    )


# =========================================================
# إنشاء بطاقة أداء أسبوعية أو شهرية
# =========================================================

def create_performance_card(
    template_path: Path,
    filename: str,
    date_range: str,
    total_trades: int,
    winning_trades: int,
    losing_trades: int,
    gross_profit: float,
    gross_loss: float,
    best_trade: float,
    worst_trade: float,
) -> BytesIO:

    if not template_path.exists():
        raise FileNotFoundError(
            f"Template image was not found: "
            f"{template_path.name}"
        )

    if total_trades <= 0:
        raise ValueError(
            "Total trades must be greater than zero"
        )

    if winning_trades < 0 or losing_trades < 0:
        raise ValueError(
            "Trade counts cannot be negative"
        )

    if (
        winning_trades
        + losing_trades
        != total_trades
    ):
        raise ValueError(
            "Winning and losing trades "
            "must equal total trades"
        )

    if (
        gross_profit < 0
        or gross_loss < 0
        or best_trade < 0
        or worst_trade < 0
    ):
        raise ValueError(
            "Financial values cannot be negative"
        )

    win_rate = (
        winning_trades
        / total_trades
    ) * 100

    net_profit = (
        gross_profit
        - gross_loss
    )

    net_profit_sar = (
        net_profit
        * USD_TO_SAR
    )

    if net_profit >= 0:
        net_text = format_profit(
            net_profit
        )

        sar_text = format_sar(
            net_profit_sar
        )

        net_color = GREEN_TEXT_COLOR

    else:
        net_text = format_loss(
            net_profit
        )

        sar_text = format_sar(
            net_profit_sar,
            include_sign=True,
        )

        net_color = RED_TEXT_COLOR

    with Image.open(
        template_path
    ) as template:
        image = template.convert("RGB")

    draw = ImageDraw.Draw(image)
    width, height = image.size

    values = [
        (
            str(total_trades),
            PERFORMANCE_TOTAL_TRADES_BOX,
            PERFORMANCE_NUMBER_FONT_RATIO,
            GOLD_TEXT_COLOR,
        ),
        (
            str(winning_trades),
            PERFORMANCE_WINNING_TRADES_BOX,
            PERFORMANCE_NUMBER_FONT_RATIO,
            GREEN_TEXT_COLOR,
        ),
        (
            str(losing_trades),
            PERFORMANCE_LOSING_TRADES_BOX,
            PERFORMANCE_NUMBER_FONT_RATIO,
            RED_TEXT_COLOR,
        ),
        (
            format_win_rate(win_rate),
            PERFORMANCE_WIN_RATE_BOX,
            PERFORMANCE_NUMBER_FONT_RATIO,
            GOLD_TEXT_COLOR,
        ),
        (
            format_profit(gross_profit),
            PERFORMANCE_GROSS_PROFIT_BOX,
            PERFORMANCE_MONEY_FONT_RATIO,
            GREEN_TEXT_COLOR,
        ),
        (
            format_loss(gross_loss),
            PERFORMANCE_GROSS_LOSS_BOX,
            PERFORMANCE_MONEY_FONT_RATIO,
            RED_TEXT_COLOR,
        ),
        (
            net_text,
            PERFORMANCE_NET_PROFIT_BOX,
            PERFORMANCE_MONEY_FONT_RATIO,
            net_color,
        ),
        (
            sar_text,
            PERFORMANCE_SAR_PROFIT_BOX,
            PERFORMANCE_MONEY_FONT_RATIO,
            net_color,
        ),
        (
            format_profit(best_trade),
            PERFORMANCE_BEST_TRADE_BOX,
            PERFORMANCE_TRADE_FONT_RATIO,
            GREEN_TEXT_COLOR,
        ),
        (
            format_loss(worst_trade),
            PERFORMANCE_WORST_TRADE_BOX,
            PERFORMANCE_TRADE_FONT_RATIO,
            RED_TEXT_COLOR,
        ),
        (
            date_range,
            PERFORMANCE_DATE_RANGE_BOX,
            PERFORMANCE_DATE_FONT_RATIO,
            GOLD_TEXT_COLOR,
        ),
    ]

    for (
        text,
        proportional_box,
        font_ratio,
        text_color,
    ) in values:

        draw_text_in_box(
            draw=draw,
            text=text,
            box=scale_box(
                proportional_box,
                width,
                height,
            ),
            card_width=width,
            maximum_font_ratio=font_ratio,
            text_color=text_color,
        )

    return save_image_to_buffer(
        image=image,
        filename=filename,
    )


# =========================================================
# إنشاء بطاقة الأداء الأسبوعي
# =========================================================

def create_weekly_card(
    total_trades: int,
    winning_trades: int,
    losing_trades: int,
    gross_profit: float,
    gross_loss: float,
    best_trade: float,
    worst_trade: float,
) -> BytesIO:

    return create_performance_card(
        template_path=WEEKLY_TEMPLATE,
        filename=(
            "quiet_alpha_weekly_results_card.jpg"
        ),
        date_range=get_week_range(),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        best_trade=best_trade,
        worst_trade=worst_trade,
    )


# =========================================================
# إنشاء بطاقة الأداء الشهري
# =========================================================

def create_monthly_card(
    total_trades: int,
    winning_trades: int,
    losing_trades: int,
    gross_profit: float,
    gross_loss: float,
    best_trade: float,
    worst_trade: float,
) -> BytesIO:

    return create_performance_card(
        template_path=MONTHLY_TEMPLATE,
        filename=(
            "quiet_alpha_monthly_results_card.jpg"
        ),
        date_range=get_month_range(),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        best_trade=best_trade,
        worst_trade=worst_trade,
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
) -> None:

    card = create_success_card(
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
) -> None:

    card = create_highest_price_card(
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
) -> None:

    card = create_stop_loss_card(
        entry_price=entry_price,
    )

    await send_card_to_channel(
        context=context,
        card=card,
    )


# =========================================================
# إرسال بطاقة الأداء الأسبوعي
# =========================================================

async def publish_weekly_card(
    context: ContextTypes.DEFAULT_TYPE,
    total_trades: int,
    winning_trades: int,
    losing_trades: int,
    gross_profit: float,
    gross_loss: float,
    best_trade: float,
    worst_trade: float,
) -> None:

    card = create_weekly_card(
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        best_trade=best_trade,
        worst_trade=worst_trade,
    )

    await send_card_to_channel(
        context=context,
        card=card,
    )


# =========================================================
# إرسال بطاقة الأداء الشهري
# =========================================================

async def publish_monthly_card(
    context: ContextTypes.DEFAULT_TYPE,
    total_trades: int,
    winning_trades: int,
    losing_trades: int,
    gross_profit: float,
    gross_loss: float,
    best_trade: float,
    worst_trade: float,
) -> None:

    card = create_monthly_card(
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        best_trade=best_trade,
        worst_trade=worst_trade,
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

        "📊 الأداء الأسبوعي:\n"
        "<code>/weekly 30 20 10 2480 620 680 260</code>\n\n"

        "📅 الأداء الشهري:\n"
        "<code>/monthly 30 20 10 2480 620 680 260</code>"
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

async def win_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/win 3.90 4.90\n\n"
            "الرقم الأول: سعر الدخول\n"
            "الرقم الثاني: السعر المحقق"
        )
        return

    try:
        entry_price = format_premium(
            context.args[0]
        )

        exit_price = format_premium(
            context.args[1]
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
            context=context,
            entry_price=entry_price,
            exit_price=exit_price,
        )

        await update.message.reply_text(
            "✅ تم إنشاء ونشر بطاقة الصفقة الناجحة."
        )

    except (ValueError, TypeError):
        await update.message.reply_text(
            "❌ سعر الدخول والسعر المحقق "
            "يجب أن يكونا أرقامًا صحيحة."
        )

    except FileNotFoundError as error:
        logger.exception(
            "Success template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف success_card.jpg.\n"
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

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/max 3.90 8.40\n\n"
            "الرقم الأول: سعر الدخول\n"
            "الرقم الثاني: أعلى سعر"
        )
        return

    try:
        entry_price = format_premium(
            context.args[0]
        )

        highest_price = format_premium(
            context.args[1]
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
            "❌ سعر الدخول وأعلى سعر "
            "يجب أن يكونا أرقامًا صحيحة.\n\n"
            "مثال:\n"
            "/max 3.90 8.40"
        )

    except FileNotFoundError as error:
        logger.exception(
            "Highest-price template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف "
            "highest_price_card.jpg.\n"
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
# =========================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/stop 3.90\n\n"
            "اكتبي سعر الدخول فقط."
        )
        return

    try:
        entry_price = format_premium(
            context.args[0]
        )

        loss_value = calculate_stop_loss(
            entry_price=float(entry_price),
        )

        await publish_stop_loss_card(
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
            "❌ سعر الدخول يجب أن يكون "
            "رقمًا صحيحًا.\n\n"
            "مثال:\n"
            "/stop 3.90"
        )

    except FileNotFoundError as error:
        logger.exception(
            "Stop-loss template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف Stop-Loss.jpg.\n"
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
# قراءة بيانات بطاقة الأداء
# =========================================================

def parse_performance_args(
    args: list[str],
) -> tuple[int, int, int, float, float, float, float]:

    if len(args) != 7:
        raise ValueError(
            "Seven values are required"
        )

    total_trades = int(
        args[0]
    )

    winning_trades = int(
        args[1]
    )

    losing_trades = int(
        args[2]
    )

    gross_profit = float(
        args[3]
    )

    gross_loss = float(
        args[4]
    )

    best_trade = float(
        args[5]
    )

    worst_trade = float(
        args[6]
    )

    if total_trades <= 0:
        raise ValueError(
            "Total trades must be greater than zero"
        )

    if (
        winning_trades
        + losing_trades
        != total_trades
    ):
        raise ValueError(
            "Winning plus losing must equal total"
        )

    if (
        gross_profit < 0
        or gross_loss < 0
        or best_trade < 0
        or worst_trade < 0
    ):
        raise ValueError(
            "Financial values cannot be negative"
        )

    return (
        total_trades,
        winning_trades,
        losing_trades,
        gross_profit,
        gross_loss,
        best_trade,
        worst_trade,
    )


# =========================================================
# إرسال ملخص الأداء بعد نشر البطاقة
# =========================================================

async def send_performance_summary(
    update: Update,
    title: str,
    total_trades: int,
    winning_trades: int,
    gross_profit: float,
    gross_loss: float,
) -> None:

    if update.message is None:
        return

    win_rate = (
        winning_trades
        / total_trades
    ) * 100

    net_profit = (
        gross_profit
        - gross_loss
    )

    if net_profit >= 0:
        net_result = format_profit(
            net_profit
        )
    else:
        net_result = format_loss(
            net_profit
        )

    sar_result = format_sar(
        net_profit * USD_TO_SAR,
        include_sign=(net_profit < 0),
    )

    await update.message.reply_text(
        f"✅ تم إنشاء ونشر {title}.\n\n"
        f"📊 نسبة النجاح: "
        f"{format_win_rate(win_rate)}\n"
        f"💰 صافي النتيجة: "
        f"{net_result}\n"
        f"🇸🇦 بالريال: "
        f"{sar_result}"
    )


# =========================================================
# أمر /weekly
# =========================================================

async def weekly_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 7:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/weekly 30 20 10 2480 620 680 260\n\n"
            "الترتيب:\n"
            "1️⃣ إجمالي الصفقات\n"
            "2️⃣ الصفقات الرابحة\n"
            "3️⃣ الصفقات الخاسرة\n"
            "4️⃣ إجمالي الربح\n"
            "5️⃣ إجمالي الخسارة\n"
            "6️⃣ أفضل صفقة\n"
            "7️⃣ أسوأ صفقة"
        )
        return

    try:
        (
            total_trades,
            winning_trades,
            losing_trades,
            gross_profit,
            gross_loss,
            best_trade,
            worst_trade,
        ) = parse_performance_args(
            context.args
        )

        await publish_weekly_card(
            context=context,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            best_trade=best_trade,
            worst_trade=worst_trade,
        )

        await send_performance_summary(
            update=update,
            title="الإحصائية الأسبوعية",
            total_trades=total_trades,
            winning_trades=winning_trades,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
        )

    except (
        ValueError,
        TypeError,
        ZeroDivisionError,
    ):
        await update.message.reply_text(
            "❌ البيانات غير صحيحة.\n\n"
            "تأكدي أن الرابحة + الخاسرة "
            "يساوي إجمالي الصفقات.\n\n"
            "مثال:\n"
            "/weekly 30 20 10 2480 620 680 260"
        )

    except FileNotFoundError as error:
        logger.exception(
            "Weekly template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف "
            "weekly_results_card.jpg.\n"
            f"{error}"
        )

    except Exception as error:
        logger.exception(
            "Failed to publish weekly card"
        )

        await update.message.reply_text(
            "❌ تعذر إنشاء البطاقة الأسبوعية.\n"
            f"نوع الخطأ: {type(error).__name__}"
        )


# =========================================================
# أمر /monthly
# =========================================================

async def monthly_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    if len(context.args) != 7:
        await update.message.reply_text(
            "❌ الاستخدام الصحيح:\n\n"
            "/monthly 30 20 10 2480 620 680 260\n\n"
            "الترتيب:\n"
            "1️⃣ إجمالي الصفقات\n"
            "2️⃣ الصفقات الرابحة\n"
            "3️⃣ الصفقات الخاسرة\n"
            "4️⃣ إجمالي الربح\n"
            "5️⃣ إجمالي الخسارة\n"
            "6️⃣ أفضل صفقة\n"
            "7️⃣ أسوأ صفقة"
        )
        return

    try:
        (
            total_trades,
            winning_trades,
            losing_trades,
            gross_profit,
            gross_loss,
            best_trade,
            worst_trade,
        ) = parse_performance_args(
            context.args
        )

        await publish_monthly_card(
            context=context,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            best_trade=best_trade,
            worst_trade=worst_trade,
        )

        await send_performance_summary(
            update=update,
            title="الإحصائية الشهرية",
            total_trades=total_trades,
            winning_trades=winning_trades,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
        )

    except (
        ValueError,
        TypeError,
        ZeroDivisionError,
    ):
        await update.message.reply_text(
            "❌ البيانات غير صحيحة.\n\n"
            "تأكدي أن الرابحة + الخاسرة "
            "يساوي إجمالي الصفقات.\n\n"
            "مثال:\n"
            "/monthly 30 20 10 2480 620 680 260"
        )

    except FileNotFoundError as error:
        logger.exception(
            "Monthly template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف "
            "monthly_results_card.jpg.\n"
            f"{error}"
        )

    except Exception as error:
        logger.exception(
            "Failed to publish monthly card"
        )

        await update.message.reply_text(
            "❌ تعذر إنشاء البطاقة الشهرية.\n"
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

    templates = {
        "call_card.jpg": CALL_TEMPLATE.exists(),
        "put_card.jpg": PUT_TEMPLATE.exists(),
        "success_card.jpg": SUCCESS_TEMPLATE.exists(),
        "highest_price_card.jpg": (
            HIGHEST_PRICE_TEMPLATE.exists()
        ),
        "Stop-Loss.jpg": (
            STOP_LOSS_TEMPLATE.exists()
        ),
        "weekly_results_card.jpg": (
            WEEKLY_TEMPLATE.exists()
        ),
        "monthly_results_card.jpg": (
            MONTHLY_TEMPLATE.exists()
        ),
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
            "✅ قالب STOP LOSS موجود.\n"
            "✅ قالب WEEKLY موجود.\n"
            "✅ قالب MONTHLY موجود."
        )

    else:
        message = (
            "🟠 البوت يعمل، لكن القوالب "
            "التالية غير موجودة:\n"
            + "\n".join(
                missing_templates
            )
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
            "weekly",
            weekly_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "monthly",
            monthly_command,
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

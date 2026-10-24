"""Draw live trade fields on the approved, versioned Quiet Alpha templates."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from .occ import parse_occ, strike_text

BASE = Path(__file__).resolve().parent.parent
GOLD = '#F7D982'
GREEN = '#70F3A0'
RED = '#FF3949'
WHITE = '#FFF8E6'
FONT_PATHS = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
)


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Card values must be finite')
    return result


def amount(value):
    return format(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')


def profit(value):
    return ('+' if value >= 0 else '-') + '$' + amount(abs(value))


def contract_text(symbol):
    if not symbol:
        return ''  # Legacy manual commands have no contract identity.
    occ = parse_occ(symbol)
    if occ is None:
        raise ValueError('Invalid option symbol')
    return f'{occ.root} · {strike_text(occ.strike)} {occ.right}'


def draw_field(draw, text, box, color, maximum=80):
    if not text:
        return
    path = next((p for p in FONT_PATHS if Path(p).exists()), None)
    if path is None:
        raise RuntimeError('A TrueType font is required to render result cards')
    left, top, right, bottom = box
    for size in range(maximum, 9, -1):
        font = ImageFont.truetype(path, size)
        bounds = draw.textbbox((0, 0), text, font=font)
        w, h = bounds[2]-bounds[0], bounds[3]-bounds[1]
        if w <= right-left-24 and h <= bottom-top-16:
            break
    else:
        raise ValueError('Card text exceeds available space')
    draw.text(((left+right-w)/2-bounds[0], (top+bottom-h)/2-bounds[1]),
              text, font=font, fill=color)


def render(kind, values, option_symbol='', date_text=None):
    with Image.open(BASE / f'qa_{kind}_v2.png') as template:
        image = template.convert('RGB')
    draw = ImageDraw.Draw(image)
    sx, sy = image.width/1536, image.height/1024
    def field(text, box, color=GOLD, maximum=80):
        scaled = tuple(v*(sx if i % 2 == 0 else sy) for i, v in enumerate(box))
        draw_field(draw, text, scaled, color, int(maximum*sx))
    field(contract_text(option_symbol), (100,490,1436,620), maximum=90)
    if kind == 'highest':
        boxes = [(52,780,386,899),(424,780,750,899),(788,780,1114,899),(1150,780,1485,899)]
    elif kind == 'success':
        boxes = [(72,775,506,890),(555,775,979,890),(1040,775,1465,890)]
    else:
        boxes = [(50,755,500,866),(545,755,997,866),(1040,755,1490,866)]
    for (text, color), box in zip(values, boxes):
        field(text, box, color)
    if date_text is None:
        now = datetime.now(ZoneInfo('Asia/Riyadh'))
        months = ('JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC')
        date_text = f'{now.day:02d} {months[now.month-1]} {now.year}'
    field(date_text, (650 if kind == 'stop' else 590,918,942,987), maximum=30)
    result = BytesIO()
    image.save(result, format='JPEG', quality=95)
    result.seek(0)
    result.name = f'quiet_alpha_{kind}_card.jpg'
    return result


def create_success_card(entry_price, exit_price, option_symbol='', date_text=None):
    entry, target = number(entry_price), number(exit_price)
    if entry <= 0 or target <= entry:
        raise ValueError('Success price must exceed positive entry')
    return render('success', [(profit((target-entry)*100),GREEN),
                              ('$'+amount(target),GOLD),('$'+amount(entry),GOLD)],
                  option_symbol, date_text)


def create_highest_price_card(entry_price, highest_price, option_symbol='', date_text=None):
    entry, high = number(entry_price), number(highest_price)
    if entry <= 0 or high <= entry:
        raise ValueError('Highest price must exceed positive entry')
    percentage = ((high-entry)/entry*100).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)
    return render('highest', [('$'+amount(entry),WHITE),('$'+amount(high),WHITE),
                              (f'+{percentage}%',GREEN),(profit((high-entry)*100),GREEN)],
                  option_symbol, date_text)


def create_stop_loss_card(entry_price, exit_price=None, option_symbol='', date_text=None):
    entry = number(entry_price)
    # The legacy manual /stop command represents the configured 50% stop.
    exit_value = entry*Decimal('.5') if exit_price is None else number(exit_price)
    if entry <= 0 or exit_value < 0 or exit_value > entry:
        raise ValueError('Invalid stop price')
    return render('stop', [('$'+amount(entry),WHITE),('$'+amount(exit_value),WHITE),
                           (profit((exit_value-entry)*100),RED)], option_symbol, date_text)

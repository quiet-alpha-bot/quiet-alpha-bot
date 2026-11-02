"""HERO OR ZERO metadata, approved copy, and end-of-tracking card."""
from decimal import Decimal

MODE = 'HERO_OR_ZERO'
LABEL = 'HERO OR ZERO'
CAPTION = ('🦋 QUIET ALPHA | HERO OR ZERO\n'
           '⚡ عقد عالي المخاطرة — بدون وقف خسارة\n'
           '⚠️ قد يفقد العقد كامل قيمته\n'
           '🛡️ الأرباح جزء من رأس المال؛ لا نجازف بها ولا ندخل لتعويض خسارة')
MANUAL_BLOCK = 'عقود HERO OR ZERO تستمر حتى نهاية مدة التتبع؛ الإغلاق اليدوي غير متاح لها.'


def is_hero(row):
    return bool(row and ((row.get('approval_quote') or {}).get('trade_mode') == MODE
                        or (row.get('details') or {}).get('trade_mode') == MODE))


def price(value):
    return 'غير متاح' if value is None else f'${Decimal(value):.2f}'


def end_caption(trade):
    return (f"🦋 QUIET ALPHA | {LABEL}\nانتهاء المتابعة — أعلى سعر مرصود\n"
            f"{trade['option_symbol']}\nسعر الدخول: {price(trade['approved_entry'])}\n"
            f"أعلى سعر وصل إليه العقد أثناء المتابعة: {price(trade['highest_bid'])}\n"
            f"آخر سعر مرصود عند انتهاء المتابعة: {price(trade['current_bid'])}")


def end_card(trade):
    # Extend the established code-rendered highest-price template, including
    # losing/no-quote contracts. No invented zero or implied exit at the peak.
    from io import BytesIO
    from PIL import Image, ImageDraw
    from .result_cards import render, draw_field, GOLD, WHITE
    base = render('highest', [], trade['option_symbol'], contract_name=LABEL)
    with Image.open(base) as source:
        image = source.convert('RGB')
    draw = ImageDraw.Draw(image)
    sx, sy = image.width/1536, image.height/1024
    def box(coords):return tuple(v*(sx if i%2==0 else sy) for i,v in enumerate(coords))
    draw.rectangle(box((42,635,1494,907)),fill='#080808')
    draw_field(draw,LABEL,box((50,635,1486,686)),GOLD,int(30*sx))
    fields = [('ENTRY PRICE',trade['approved_entry']),('HIGHEST OBSERVED',trade['highest_bid']),
              ('LAST OBSERVED',trade['current_bid'])]
    for index,(label,value) in enumerate(fields):
        left=52+index*480
        draw.rounded_rectangle(box((left,693,left+464,900)),radius=int(16*sx),outline=GOLD,width=2)
        draw_field(draw,label,box((left,702,left+464,766)),GOLD,int(28*sx))
        text='N/A' if value is None else f'${Decimal(value):.2f}'
        draw_field(draw,text,box((left,774,left+464,890)),WHITE,int(76*sx))
    output=BytesIO();image.save(output,format='JPEG',quality=95);output.seek(0)
    output.name='hero_or_zero_end.jpg'
    return output

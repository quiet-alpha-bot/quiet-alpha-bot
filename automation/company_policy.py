"""Company experiment policy: independent of SPX strategy and routing."""
from decimal import Decimal
from html import escape
from .occ import parse_occ, strike_text

CHAT = '-1004471084642'
GAIN = Decimal('.50')
STOP = Decimal('.50')

def fresh(quote,symbol,now):
    return (quote is not None and quote.symbol==symbol and quote.occ is not None
        and quote.bid is not None and quote.ask is not None
        and quote.bid.is_finite() and quote.ask.is_finite()
        and 0 <= quote.bid <= quote.ask and quote.ask>0
        and quote.last_tape_time is not None
        and 0 <= (now-quote.last_tape_time).total_seconds() <= 120)

def label(symbol):
    occ=parse_occ(symbol)
    return escape(f'{occ.root} {strike_text(occ.strike)} {occ.right} · {occ.expiry.isoformat()}')

def result(entry,bid):
    if bid is None:return 'غير متاح'
    delta=(Decimal(bid)-Decimal(entry))*100
    pct=(Decimal(bid)/Decimal(entry)-1)*100
    return f'{delta:+.2f}$ ({pct:+.2f}%)'

def live_text(row):
    bid=row['current_bid'];price=f'${Decimal(bid):.2f}' if bid is not None else 'غير متاح'
    status='ما زالت مفتوحة' if row['status']=='OPEN' else 'أُغلقت المتابعة'
    return (f"🦋 <b>QUIET ALPHA — متابعة الشركات #{row['id']}</b>\n\n"
        f"<b>{label(row['option_symbol'])}</b>\nسعر الدخول: <b>${Decimal(row['entry_price']):.2f}</b>\n"
        f"آخر سعر طلب مرصود: <b>{price}</b>\nالتغير للعقد: <b>{result(row['entry_price'],bid)}</b>\n"
        f"الحالة: <b>{status}</b>\n"
        f"وقت السعر (UTC): {row['last_quote_at'].isoformat() if row['last_quote_at'] else 'غير متاح'}")

def close_reason(entry,bid):
    # The 50% stop remains active even after target attainment; no breakeven protection.
    return 'STOP_LOSS_50' if bid is not None and Decimal(bid)<=Decimal(entry)*STOP else None

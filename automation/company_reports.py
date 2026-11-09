"""Company-only daily marks and deduplicated weekly experimental results."""
import asyncio
import logging
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from .company_policy import CHAT,label,result
from .company_tracking import ACTIVE
from .report_sections import send_pages
from .timeutil import utcnow,et_date,et_datetime

log=logging.getLogger('quiet-alpha.company-reports')
ROWS="""SELECT p.id,p.option_symbol,p.entry_price,p.published_at,p.expiry,
m.market_date,m.current_bid,m.highest_bid,m.lowest_bid,m.last_quote_at,m.success_at,m.status,m.close_reason
FROM company_session_marks m JOIN company_positions p ON p.id=m.position_id
WHERE m.market_date BETWEEN $1 AND $2 ORDER BY m.market_date,p.id"""
DAYS="SELECT DISTINCT market_date FROM company_session_marks WHERE market_date<=$1 ORDER BY market_date"
CARRY="""SELECT * FROM company_positions WHERE status='OPEN' AND published_at IS NOT NULL
AND (published_at AT TIME ZONE 'America/New_York')::date <= $1"""


def report(rows,day,weekly=False):
    # A contract spanning multiple sessions appears once, at its last available mark.
    unique={row['id']:row for row in rows}
    rows=list(unique.values());total=len(rows)
    hits=sum(r['success_at'] is not None for r in rows)
    opened=sum(r['status']=='OPEN' for r in rows)
    closed_no_goal=sum(r['status']=='CLOSED' and r['success_at'] is None for r in rows)
    ratio=100*hits/total if total else 0
    title='الأسبوعي' if weekly else 'اليومي'
    dates=f'{day-timedelta(days=4)} — {day}' if weekly else str(day)
    lines=[f'📊 <b>QUIET ALPHA — بيان الشركات {title}</b>',dates,'',
        f'العقود المتابَعة: <b>{total}</b>',f'✅ بلغت هدف +50$: <b>{hits}</b>',
        f'نسبة بلوغ الهدف من العقود المعروضة: <b>{ratio:.1f}%</b>',
        f'🔄 ما زالت مفتوحة: <b>{opened}</b>',f'مغلقة دون بلوغ الهدف: <b>{closed_no_goal}</b>',
        'بلوغ الهدف يُسجل مرة واحدة؛ ولا يعني إغلاق العقد.','']
    for row in rows:
        bid=row['current_bid'];price=f'${Decimal(bid):.2f}' if bid is not None else 'غير متاح'
        state='🔄 ما زالت مفتوحة' if row['status']=='OPEN' else ('🛑 أُغلقت بالوقف' if row['close_reason']=='STOP_LOSS_50' else 'انتهى تتبع العقد')
        lines.extend([f"<b>#{row['id']} · {label(row['option_symbol'])}</b>",
            f"الدخول: ${row['entry_price']:.2f} ← آخر سعر مرصود بنهاية الجلسة: {price}",
            f"التغير المرصود للعقد: {result(row['entry_price'],bid)}",state,
            '✅ سبق بلوغ الهدف' if row['success_at'] is not None else 'لم يُرصد بلوغ الهدف بعد'])
        at=row['last_quote_at']
        if at is not None:lines.append(f"وقت آخر سعر (UTC): {at.isoformat()}")
        if at is None or et_date(at)!=row['market_date']:
            lines.append('⚠️ لا يتوفر سعر حديث لهذه الجلسة؛ المعروض آخر سعر متاح، وليس إغلاقًا مؤكدًا.')
        if weekly:lines.append(f"تاريخ آخر رصد ضمن الأسبوع: {row['market_date']}")
        lines.append('')
    return '\n'.join(lines)


class CompanyReports:
    def __init__(self,tracking):
        self.tracking=tracking;self.db=tracking.db;self.publisher=tracking.publisher
        self.last_error=None

    async def tick(self):
        now=utcnow();today=et_date(now);s=self.tracking.s
        async with self.tracking.lock:
            # At close, include carried positions even if no fresh quote arrived today.
            if today.weekday()<5 and now>=et_datetime(today,s.market.close_et)+timedelta(minutes=2):
                for row in await self.db.fetch(CARRY,today):
                    await self.tracking.mark(row,today)
            days={item['market_date'] for item in await self.db.fetch(DAYS,today)}
            # Friday report still exists when the week's last position closed earlier.
            for day in list(days):
                friday=day+timedelta(days=4-day.weekday())
                if friday<=today:days.add(friday)
            for day in sorted(days):
                if day.weekday()>=5 or now<et_datetime(day,s.market.close_et)+timedelta(minutes=2):continue
                # End-of-expiry transitions must be persisted before the report freezes.
                pending=await self.db.fetchval("SELECT EXISTS(SELECT 1 FROM company_positions WHERE status='OPEN' AND expiry<=$1)",day)
                if pending:continue
                rows=await self.db.fetch(ROWS,day,day)
                if rows or day.weekday()==4:
                    await send_pages(self,'company_daily',int(day.strftime('%Y%m%d')),'REPORT',report(rows,day),CHAT)
                if day.weekday()==4:
                    week=await self.db.fetch(ROWS,day-timedelta(days=4),day)
                    if week:
                        # send_pages confirms every daily page before proceeding.
                        if await send_pages(self,'company_daily',int(day.strftime('%Y%m%d')),'REPORT',report(rows,day),CHAT):
                            await send_pages(self,'company_weekly',int(day.strftime('%Y%m%d')),'REPORT',report(week,day,True),CHAT)

    async def run(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception as error:
                self.last_error=type(error).__name__;log.exception('Company report failed')
            await asyncio.sleep(30)

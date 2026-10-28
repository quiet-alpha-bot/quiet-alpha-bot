"""Company options activity, independent of SPX selection and trade tracking.

UW active option chains discover candidates across all expiries and sizes.
Exact-contract minute aggregates measure the last five completed minutes.
Compare the same NY clock window in three recent historical trading sessions.
No baseline, missing data, or stale tape => no fabricated unusualness alert.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .occ import parse_occ, strike_text
from .timeutil import utcnow, et_date, to_et, et_datetime, display_time, within_session
from .models import parse_ts

logger = logging.getLogger('quiet-alpha.company-radar')
TICKERS = ('NVDA', 'TSLA', 'AAPL', 'AMZN', 'MSFT', 'META', 'GOOGL', 'AMD', 'AVGO', 'PLTR')
MIN_PREMIUM = Decimal('1000000')
MIN_MULTIPLE = Decimal('3')
POLL_SECONDS = 60
ZERO = Decimal('0')
EVENT = 'COMPANY_FLOW'
LATEST = """SELECT * FROM company_radar_alerts
WHERE market_date=$1 AND option_symbol=$2 ORDER BY level DESC LIMIT 1"""
INSERT = """INSERT INTO company_radar_alerts
(market_date,option_symbol,level,window_start,window_end,premium,message_text,payload)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
ON CONFLICT (market_date,option_symbol,level) DO NOTHING RETURNING *"""


class DataIssue(ValueError):
    pass


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise DataIssue('INVALID_NUMERIC_DATA') from None


def data_rows(payload, key='data'):
    rows = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise DataIssue('INVALID_RESPONSE_SHAPE')
    if key == 'data' and len(rows) == 1 and isinstance(rows[0].get('data'), list):
        rows = rows[0]['data']
        if any(not isinstance(r, dict) for r in rows):
            raise DataIssue('INVALID_RESPONSE_SHAPE')
    return rows


@dataclass(frozen=True)
class Minute:
    at: object
    premium: Decimal
    ask: Decimal
    bid: Decimal
    volume: int


def minutes(payload, symbol, day):
    result = {}
    for row in data_rows(payload):
        actual = row.get('option_symbol')
        if actual != symbol:
            # Only schema names and valid contract IDs: never dump raw API data.
            keys = ','.join(sorted(k for k in row if isinstance(k,str)
                                  and k.replace('_','').isalnum())[:20])
            if actual is None or actual == '':
                raise DataIssue(f'CONTRACT_SYMBOL_MISSING expected={symbol}; fields={keys}')
            received = actual if isinstance(actual,str) and parse_occ(actual) is not None else 'INVALID_SYMBOL'
            raise DataIssue(f'CONTRACT_MISMATCH expected={symbol}; received={received}')
        at = parse_ts(row.get('start_time'))
        if at is None or et_date(at) != day or at.second or at.microsecond:
            raise DataIssue('INVALID_MINUTE_TIME')
        premiums = [number(row.get('premium_'+s+'_side')) for s in ('ask','bid','mid','no')]
        volumes = [number(row.get('volume_'+s+'_side')) for s in ('ask','bid','mid','no')]
        if any(v != v.to_integral_value() for v in volumes):
            raise DataIssue('INVALID_VOLUME')
        minute = Minute(at, sum(premiums,ZERO), premiums[0], premiums[1], int(sum(volumes)))
        if at in result and result[at] != minute:
            raise DataIssue('CONFLICTING_MINUTE')
        result[at] = minute
    return list(result.values())


def window(rows, start, end):
    return [m for m in rows if start <= m.at < end]


def historical_days(payload, today):
    days = {}
    for row in data_rows(payload, 'chains'):
        try:
            day = date.fromisoformat(str(row['date']))
        except (KeyError, ValueError):
            raise DataIssue('INVALID_HISTORY_DATE') from None
        if today-timedelta(days=10) <= day < today:
            volume = number(row.get('volume'))
            if day in days and days[day] != volume:
                raise DataIssue('CONFLICTING_HISTORY')
            days[day] = volume
    if len(days) < 3:
        raise DataIssue('INSUFFICIENT_HISTORY')
    return [(d, days[d]) for d in sorted(days, reverse=True)[:3]]


def measure(rows, start, end, now):
    selected = window(rows,start,end)
    active = [m for m in selected if m.volume > 0]
    if not active or not 0 <= (now-max(m.at for m in active)).total_seconds() <= 120:
        return None
    total = sum((m.premium for m in selected),ZERO)
    if total < MIN_PREMIUM:
        return None
    return {'premium':total, 'volume':sum(m.volume for m in selected),
            'ask':sum((m.ask for m in selected),ZERO),
            'bid':sum((m.bid for m in selected),ZERO)}


def company_text(symbol, start, end, values, average, now):
    occ = parse_occ(symbol)
    side = ('Ask' if values['ask']/values['premium'] >= Decimal('.6') else
            'Bid' if values['bid']/values['premium'] >= Decimal('.6') else 'مختلط / غير حاسم')
    ratio = values['premium']/average
    analysis = {'Ask':'غلبة التنفيذ قرب Ask؛ لا تكفي وحدها لإثبات فتح مراكز شراء جديدة.',
                'Bid':'غلبة التنفيذ قرب Bid؛ لا تكفي وحدها لتحديد فتح المراكز أو إغلاقها.'}.get(
                    side,'التنفيذ موزّع بين الجانبين، ولا يظهر ميل واضح في هذه الفترة.')
    return (
        '🦋 <b>QUIET ALPHA — رادار سيولة الشركات</b>\n\n'
        f'<b>{occ.root}</b>\n'
        f"{'🟢' if occ.right=='CALL' else '🔴'} <b>{occ.right} {strike_text(occ.strike)}</b>\n"
        f'📅 الانتهاء: <b>{occ.expiry:%d %b %Y}</b>\n\n'
        f"💰 قيمة التدفقات: <b>${values['premium']/1000000:.2f}M</b>\n"
        f"📦 حجم التداول: <b>{values['volume']:,} عقد</b>\n"
        f'⏱ فترة الرصد: <b>{to_et(start):%H:%M}–{to_et(end):%H:%M} نيويورك · 5 دقائق</b>\n'
        f'📊 النشاط: <b>{ratio:.1f}×</b> متوسط قيمة التدفقات لنفس الفترة في 3 جلسات سابقة\n'
        f'🔎 جانب التنفيذ الغالب: <b>{side}</b>\n\n'
        '🦋 <b>QUIET ALPHA — تحليل السيولة</b>\n'
        f"{'نشاط كبير وغير اعتيادي' if values['premium'] >= 3000000 else 'نشاط غير اعتيادي'} "
        f'على العقد نفسه خلال فترة الرصد. {analysis}\n\n'
        f"🕒 السعودية: <b>{display_time(now,'Asia/Riyadh')}</b>\n\n"
        '⚠️ <b>تنبيه</b>\n'
        'المحتوى قراءة تحليلية لتدفقات الخيارات المنفّذة، ولا يُعدّ توصية دخول أو شراء أو بيع.'
    )


class CompanyRadar:
    latest_query = LATEST
    insert_query = INSERT
    render_text = staticmethod(company_text)

    def __init__(self, settings, db, publisher, uw):
        self.s, self.db, self.publisher, self.uw = settings, db, publisher, uw
        self.last_error = None
        self.last_check_at = None
        self.scanned = 0
        self.insufficient_history = 0
        self._history = {}
        self._day = None

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = str(error) if isinstance(error,DataIssue) else getattr(error,'kind',type(error).__name__)
                logger.exception('Company radar cycle failed')
            await asyncio.sleep(POLL_SECONDS)

    async def candidates(self, ticker, now):
        found = {}
        # Separate ticker scans prevent a busy stock from crowding out others.
        for page in range(20):
            response = await self.uw.get(f'/api/stock/{ticker}/option-contracts', {
                'exclude_zero_vol_chains':'true', 'limit':500, 'page':page,
            })
            rows = data_rows(response.data)
            for row in rows:
                symbol = row.get('option_symbol')
                occ = parse_occ(symbol)
                if occ is None or occ.root != ticker:
                    raise DataIssue('CHAIN_SYMBOL_MISMATCH')
                at = parse_ts(row.get('last_tape_time'))
                if (occ.expiry >= et_date(now) and at is not None
                    and 0 <= (now-at).total_seconds() <= 120
                    and number(row.get('total_premium')) >= MIN_PREMIUM):
                    found[symbol] = number(row['total_premium'])
            if len(rows) < 500:
                return sorted(found,key=found.get,reverse=True)
        raise DataIssue('COMPANY_CHAIN_PAGE_LIMIT')

    async def baseline(self, symbol, start, end):
        day = et_date(end)
        if symbol not in self._history:
            response = await self.uw.get(f'/api/option-contract/{symbol}/historic',{'limit':10})
            dates = historical_days(response.data, day)
            history = []
            for previous, daily_volume in dates:
                response = await self.uw.get(f'/api/option-contract/{symbol}/intraday',
                                             {'date':previous.isoformat()})
                bars = minutes(response.data,symbol,previous)
                # A partial/empty history must never become a zero baseline.
                if not bars or sum(m.volume for m in bars) != daily_volume:
                    raise DataIssue('INCOMPLETE_HISTORY')
                history.append((previous,bars))
            self._history[symbol] = history
        premiums = []
        for previous,bars in self._history[symbol]:
            a = et_datetime(previous,to_et(start).time())
            b = et_datetime(previous,to_et(end).time())
            chosen = window(bars,a,b)
            # No prints in the historical window is not evidence of a normal rate.
            if not chosen:
                raise DataIssue('INSUFFICIENT_HISTORY')
            premiums.append(sum((m.premium for m in chosen),ZERO))
        average = sum(premiums,ZERO)/3
        if average <= 0:
            raise DataIssue('INSUFFICIENT_HISTORY')
        return average

    async def tick(self):
        now = utcnow()
        if not within_session(now,self.s.market.open_et,self.s.market.close_et):
            return
        end = now.replace(second=0,microsecond=0)
        start = end-timedelta(minutes=5)
        if start < et_datetime(et_date(now),self.s.market.open_et):
            return
        if self._day != et_date(now):
            self._day = et_date(now)
            self._history.clear()
        self.last_check_at, self.last_error = now, None
        self.scanned = self.insufficient_history = 0
        # Bounded work per ticker in a separate leader task from price tracking.
        for ticker in TICKERS:
            try:
                symbols = await self.candidates(ticker,utcnow())
                if len(symbols) > 60:
                    raise DataIssue('COMPANY_CONTRACT_BUDGET')
                for symbol in symbols:
                    try:
                        await self.check(symbol,start,end)
                        self.scanned += 1
                    except DataIssue as error:
                        if str(error) in ('INSUFFICIENT_HISTORY','INCOMPLETE_HISTORY'):
                            self.insufficient_history += 1
                        else:
                            self.last_error = str(error)
            except Exception as error:
                self.last_error = str(error) if isinstance(error,DataIssue) else getattr(error,'kind',type(error).__name__)
                logger.warning('Company radar %s: %s',ticker,self.last_error)

    async def check(self, symbol, start, end):
        if not 0 <= (utcnow()-end).total_seconds() <= 120:
            return
        response = await self.uw.get(f'/api/option-contract/{symbol}/intraday',{'date':et_date(end).isoformat()})
        bars = minutes(response.data,symbol,et_date(end))
        values = measure(bars,start,end,utcnow())
        if values is None:
            return
        average = await self.baseline(symbol,start,end)
        if values['premium'] < average*MIN_MULTIPLE:
            return
        now = utcnow()
        if (not within_session(now,self.s.market.open_et,self.s.market.close_et)
            or measure(bars,start,end,now) is None or not 0 <= (now-end).total_seconds() <= 120):
            return
        previous = await self.db.fetchrow(self.latest_query,et_date(end),symbol)
        if previous is not None:
            if 0 <= (now-previous['window_end']).total_seconds() <= 120:
                # Frozen text/evidence; retry definite delivery failures only.
                await self.send(previous)
                return
            if (start < previous['window_end']
                or end-previous['window_end'] < timedelta(minutes=15)
                or values['premium'] < previous['premium']*Decimal('1.5')):
                return
        payload = {'symbol':symbol,'window_start':start.isoformat(),'window_end':end.isoformat(),
                   'premium':str(values['premium']),'volume':values['volume'],
                   'ask':str(values['ask']),'bid':str(values['bid']),
                   'baseline':str(average),'baseline_days':[str(d) for d,_ in self._history[symbol]]}
        row = await self.db.fetchrow(self.insert_query,et_date(end),symbol,
                    0 if previous is None else previous['level']+1,
                    start,end,values['premium'],self.render_text(symbol,start,end,values,average,now),payload)
        if row is not None:
            await self.send(row)

    async def send(self, row):
        await self.publisher.text_once('company_radar',row['id'],EVENT,row['message_text'],
                                       payload=row['payload'],max_attempts=3)

"""Durable company positions; a confirmed radar publication starts the experiment."""
import asyncio
import logging
from decimal import Decimal
from datetime import timedelta
from . import queries as q
from .company_policy import CHAT, GAIN, fresh, label, live_text, close_reason, result
from .occ import parse_occ
from .models import parse_ts
from .company_delivery import CompanyPublisher
from .timeutil import utcnow, et_date, et_datetime, within_session

log=logging.getLogger('quiet-alpha.company-tracking')
GET='SELECT * FROM company_positions WHERE id=$1'
ACTIVE="SELECT * FROM company_positions WHERE status IN ('PENDING','OPEN') ORDER BY id"
INSERT="""INSERT INTO company_positions(alert_id,option_symbol,expiry,entry_day,entry_price,entry_quote_at,entry_text)
VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING RETURNING *"""
OPEN="""UPDATE company_positions SET status='OPEN',published_at=$2 WHERE id=$1 AND status='PENDING' RETURNING *"""
OBSERVE="""UPDATE company_positions SET current_bid=$2,highest_bid=GREATEST(highest_bid,$2),
lowest_bid=LEAST(lowest_bid,$2),last_quote_at=$3,
success_at=CASE WHEN success_at IS NULL AND $2>=entry_price+0.50 THEN $3 ELSE success_at END
WHERE id=$1 AND status='OPEN' AND (last_quote_at IS NULL OR $3>=last_quote_at) RETURNING *"""
CLOSE="""UPDATE company_positions SET status='CLOSED',closed_at=$2,close_reason=$3
WHERE id=$1 AND status='OPEN' RETURNING *"""
MARK="""INSERT INTO company_session_marks(position_id,market_date,current_bid,highest_bid,lowest_bid,
last_quote_at,success_at,status,close_reason) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
ON CONFLICT(position_id,market_date) DO UPDATE SET current_bid=EXCLUDED.current_bid,
highest_bid=EXCLUDED.highest_bid,lowest_bid=EXCLUDED.lowest_bid,last_quote_at=EXCLUDED.last_quote_at,
success_at=EXCLUDED.success_at,status=EXCLUDED.status,close_reason=EXCLUDED.close_reason"""
RETRY="""SELECT p.* FROM company_positions p WHERE p.status IN ('OPEN','CLOSED') AND (
(p.success_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM events e WHERE e.entity_type='company_position'
 AND e.entity_id=p.id AND e.event_type='SUCCESS' AND e.status IN ('SENT','UNCERTAIN','NEEDS_RECONCILIATION')))
OR (p.status='CLOSED' AND NOT EXISTS(SELECT 1 FROM events e WHERE e.entity_type='company_position'
 AND e.entity_id=p.id AND e.event_type='CLOSE' AND e.status IN ('SENT','UNCERTAIN','NEEDS_RECONCILIATION'))))"""

class CompanyTracking:
    def __init__(self,settings,db,bot,uw):
        self.s,self.db,self.uw=settings,db,uw
        self.publisher=CompanyPublisher(bot,db)
        self.last_error=None;self.last_check_at=None
        self.lock=asyncio.Lock()

    async def quotes(self,symbols):
        # UW quotes_for() is configured for SPX. Company chains must use their own ticker.
        grouped={};out={}
        for symbol in symbols:
            occ=parse_occ(symbol)
            if occ:grouped.setdefault(occ.root,[]).append(symbol)
        for ticker,requested in grouped.items():
            for offset in range(0,len(requested),100):
                batch=requested[offset:offset+100]
                rows,_=await self.uw.option_contracts(ticker,symbols=batch)
                out.update({qt.symbol:qt for qt in rows if qt.symbol in batch})
        return out

    async def offer(self,alert):
        async with self.lock:
            now=utcnow()
            if not self.s.flags.tracking:return
            if not within_session(now,self.s.market.open_et,self.s.market.close_et):return
            if not 0 <= (now-alert['window_end']).total_seconds() <= 120:return
            quotes=await self.quotes([alert['option_symbol']]);quote=quotes.get(alert['option_symbol'])
            now=utcnow()
            if not fresh(quote,alert['option_symbol'],now) or quote.occ.expiry<et_date(now):return
            if not within_session(now,self.s.market.open_et,self.s.market.close_et):return
            body=(alert['message_text']+'\n\n<b>بدء متابعة تجريبية للعقد</b>\n'
                f'سعر الدخول المرجعي (Ask): <b>${quote.ask:.2f}</b>\n'
                f'قيمة عقد واحد: <b>${quote.ask*100:.2f}</b>\n'
                f'تاريخ الانتهاء: {quote.occ.expiry.isoformat()}\n'
                'الهدف المرصود: +50$ للعقد · الوقف: 50% من سعر الدخول.\n'
                'تستمر المتابعة بعد الهدف حتى الوقف أو نهاية جلسة الانتهاء.')
            row=await self.db.fetchrow(INSERT,alert['id'],quote.symbol,quote.occ.expiry,et_date(now),quote.ask,quote.last_tape_time,body)
            if row:await self.start(row,now)

    async def start(self,row,now):
        event=await self.db.fetchrow(q.EVENT_STATUS,'company_position',row['id'],'ENTRY',0)
        if event and event['status']=='SENT':
            row=await self.db.fetchrow(OPEN,row['id'],parse_ts((event.get('payload') or {}).get('telegram_sent_at')) or now)
            return row
        if event and event['status'] not in ('FAILED',):return None
        # Do not publish an old frozen quote after an outage. A fresh radar alert can
        # create another day's position; no entry is fabricated retrospectively.
        if (not within_session(now,self.s.market.open_et,self.s.market.close_et)
            or not 0 <= (now-row['entry_quote_at']).total_seconds() <= 120):
            await self.db.execute("UPDATE company_positions SET status='CANCELLED' WHERE id=$1 AND status='PENDING'",row['id'])
            return None
        await self.publisher.text_once('company_position',row['id'],'ENTRY',row['entry_text'],
            payload={'entry':str(row['entry_price']),'quote_at':row['entry_quote_at'].isoformat()},max_attempts=1000000)
        event=await self.db.fetchrow(q.EVENT_STATUS,'company_position',row['id'],'ENTRY',0)
        if event and event['status']=='SENT':
            return await self.db.fetchrow(OPEN,row['id'],parse_ts((event.get('payload') or {}).get('telegram_sent_at')) or utcnow())
        return None

    async def mark(self,row,day):
        await self.db.execute(MARK,row['id'],day,row['current_bid'],row['highest_bid'],row['lowest_bid'],
            row['last_quote_at'],row['success_at'],row['status'],row['close_reason'])

    async def notifications(self,row):
        if row['success_at'] is not None:
            await self.publisher.text_once('company_position',row['id'],'SUCCESS',
                f"🦋 <b>تم تحقيق الهدف ✅</b>\n{label(row['option_symbol'])}\n"
                f"الدخول: ${row['entry_price']:.2f} · تم رصد ارتفاع +50$ للعقد.\n"
                'تستمر متابعة العقد حتى الوقف أو نهاية جلسة الانتهاء.',max_attempts=1000000)
        if row['status']=='CLOSED':
            live=await self.db.fetchrow(q.EVENT_STATUS,'company_position',row['id'],'LIVE',0)
            if live and live['status']=='SENT' and live.get('message_id'):
                await self.publisher.edit(live['message_id'],live_text(row))
            why='وقف الخسارة 50%' if row['close_reason']=='STOP_LOSS_50' else 'نهاية جلسة تاريخ انتهاء العقد'
            high=f"${row['highest_bid']:.2f}" if row['highest_bid'] is not None else 'غير متاح'
            await self.publisher.text_once('company_position',row['id'],'CLOSE',
                live_text(row)+f'\nسبب الإغلاق: {why}\nأعلى سعر مرصود: {high}',max_attempts=1000000)

    async def observe(self,row,quote,now):
        if not within_session(now,self.s.market.open_et,self.s.market.close_et):return
        if not fresh(quote,row['option_symbol'],now):return
        if quote.last_tape_time<row['published_at']:return
        if et_date(quote.last_tape_time)!=et_date(now):return
        row=await self.db.fetchrow(OBSERVE,row['id'],quote.bid,quote.last_tape_time)
        if not row:return
        reason=close_reason(row['entry_price'],row['current_bid'])
        if reason:row=await self.db.fetchrow(CLOSE,row['id'],now,reason) or row
        await self.mark(row,et_date(now))
        await self.notifications(row)
        if row['status']=='OPEN' and row['live_bid']!=row['current_bid']:
            text=live_text(row)
            await self.publisher.text_once('company_position',row['id'],'LIVE',text,max_attempts=1000000)
            event=await self.db.fetchrow(q.EVENT_STATUS,'company_position',row['id'],'LIVE',0)
            if event and event['status']=='SENT' and event.get('message_id'):
                if await self.publisher.edit(event['message_id'],text):
                    await self.db.execute('UPDATE company_positions SET live_bid=$2 WHERE id=$1',row['id'],row['current_bid'])

    async def tick(self):
        async with self.lock:
            now=utcnow();self.last_check_at=now
            for row in await self.db.fetch(RETRY):await self.notifications(row)
            active=[]
            for row in await self.db.fetch(ACTIVE):
                if row['status']=='PENDING':row=await self.start(row,now)
                if not row:continue
                if now>=et_datetime(row['expiry'],self.s.market.close_et):
                    row=await self.db.fetchrow(CLOSE,row['id'],et_datetime(row['expiry'],self.s.market.close_et),'EXPIRY')
                    if row:
                        await self.mark(row,row['expiry']);await self.notifications(row)
                    continue
                active.append(row)
            if not active or not within_session(now,self.s.market.open_et,self.s.market.close_et):return
            quotes=await self.quotes(sorted({r['option_symbol'] for r in active}))
            for row in active:
                try:await self.observe(row,quotes.get(row['option_symbol']),utcnow())
                except Exception as error:
                    self.last_error=type(error).__name__;log.exception('Company position %s failed',row['id'])

    async def run(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception as error:
                self.last_error=type(error).__name__;log.exception('Company tracking failed')
            await asyncio.sleep(max(1,self.s.tracking.poll_seconds))

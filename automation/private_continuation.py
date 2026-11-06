"""Private-only continuation experiment; independent exact-contract momentum/flow.

One five-minute search after a current +$100 per-contract gain. Samples use distinct tape
observations 30..180 seconds apart, not repeated HTTP retrievals. Child trades
cannot spawn children. Durable parent state and the normal direction lock
prevent duplicate entries across restarts and concurrent manual approvals.
"""
import asyncio
import logging
import os
from datetime import timedelta
from decimal import Decimal
from html import escape

from . import queries as q, channel_routing as routing
from .autonomous import fresh_quote, rising, GET_QUOTE, SAVE_QUOTE, ACTIVE
from .company_radar import data_rows, DataIssue
from .hero import is_hero
from .models import Direction
from .selection import select_with_flow
from .timeutil import utcnow, et_date, et_datetime, within_session

log=logging.getLogger('quiet-alpha.private-continuation')
GET='SELECT * FROM private_continuations WHERE origin_trade_id=$1'
PENDING="SELECT * FROM private_continuations ORDER BY origin_trade_id DESC LIMIT 200"
START="""INSERT INTO private_continuations(origin_trade_id,started_at,expires_at)
VALUES($1,$2,$3) ON CONFLICT(origin_trade_id) DO NOTHING"""
SKIP="UPDATE private_continuations SET status='SKIPPED',reason=$2 WHERE origin_trade_id=$1 AND status='SEARCHING'"


def enabled():
    return os.getenv('PRIVATE_CONTINUATION_ENABLED','true').lower() in ('true','1','yes','on')


def parent_eligible(trade,now):
    return (trade is not None and trade['status']=='TRACKING' and not is_hero(trade)
            and not (trade.get('approval_quote') or {}).get('continuation_origin')
            and trade['expiry']==et_date(now)
            and trade.get('publication_chat_id') in (routing.PRIVATE_CHAT,routing.BOTH_CHATS))


def threshold(trade,quote,now):
    return (fresh_quote(quote,now) and quote.symbol==trade['option_symbol']
            and Decimal(trade['approved_entry'])>0
            and quote.bid>=Decimal(trade['approved_entry'])+Decimal('1.00'))


def ordered_candidates(chain,parent,settings):
    """Nearest listed strike in the direction of travel; skip out-of-range contracts."""
    out={}
    for quote in chain:
        occ=quote.occ
        if (occ and occ.root==settings.uw.contract_root and occ.expiry==parent['expiry']
            and occ.right==parent['direction'] and quote.ask is not None
            and quote.ask.is_finite() and settings.selection.premium_min<=quote.ask<=settings.selection.premium_max
            and ((occ.right=='CALL' and occ.strike>parent['strike'])
                 or (occ.right=='PUT' and occ.strike<parent['strike']))):
            out[quote.symbol]=quote
    return sorted(out.values(),key=lambda quote:abs(quote.occ.strike-parent['strike']))


class PrivateContinuation:
    def __init__(self,rt):self.rt=rt;self.last_error=None

    async def run(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception as error:
                self.last_error=type(error).__name__;log.exception('Private continuation failed')
            await asyncio.sleep(30)

    async def momentum(self,symbol):
        rt=self.rt
        quotes,_=await rt.uw.quotes_for([symbol]);quote=quotes.get(symbol);now=utcnow()
        if quote is not None and quote.symbol!=symbol:raise DataIssue('QUOTE_CONTRACT_MISMATCH')
        prior=await rt.db.fetchrow(GET_QUOTE,symbol)
        good=rising(quote,prior,now)
        if fresh_quote(quote,now):
            await rt.db.execute(SAVE_QUOTE,symbol,quote.bid,quote.last_tape_time,now)
        return quote,good

    async def flow(self,symbol):
        rt=self.rt;now=utcnow()
        return await rt.liquidity.snapshot(max(now-timedelta(minutes=5),et_datetime(et_date(now),rt.settings.market.open_et)),
                                           now,single_leg=True,symbols=[symbol])

    def qualified(self,quote,snapshot,parent):
        if not fresh_quote(quote,utcnow()) or snapshot is None or not snapshot.complete:return False
        selected=select_with_flow([quote],Direction(parent['direction']),parent['expiry'],
                                  self.rt.settings.uw.contract_root,self.rt.settings.selection,utcnow(),snapshot)
        return selected.selected is not None

    async def notify(self,row):
        rt=self.rt;ident=row['origin_trade_id']
        if row['status']=='SKIPPED':
            await rt.publisher.text_once('continuation',ident,'SKIPPED',
                f'🦋 الصفقة الإضافية للأصل #{ident}: لم تُنشر.\n'+escape(row['reason'] or 'لم تكتمل الشروط.'))
        elif row['status']=='OPENED':
            child=await rt.db.fetchrow(q.GET_TRADE,row['child_trade_id'])
            if not child:return
            state=await rt.db.fetchrow(q.EVENT_STATUS,'trade',child['id'],'ENTRY_CARD',0)
            if state and state['status']=='SENT':
                await rt.publisher.text_once('continuation',ident,'PUBLISHED',
                    f"✅ نُشرت الصفقة الإضافية #{child['id']} تلقائيًا بالخاصة.\n"
                    f"الأصل #{ident} · <code>{escape(child['option_symbol'])}</code> · الدخول ${child['approved_entry']}\n"
                    'تحقق +100 دولار للعقد الأصلي، وصعود رصدين حديثين للأصل والعقد الجديد، وتدفقات شراء متكررة وفارق سعر ضمن الشروط.')
            elif child.get('close_reason')=='OWNER_DELETED':
                await rt.publisher.text_once('continuation',ident,'CANCELLED','أُلغيت الصفقة الإضافية بأمر الإدارة.')
            else:
                await rt.publisher.text_once('continuation',ident,'DELIVERY_PENDING',
                    f"⚠️ حُفظت الصفقة الإضافية #{child['id']} لكن لم يتأكد وصول بطاقة الدخول للخاصة؛ لم تُسجل كنشر ناجح. راجعي حالة الإرسال.")

    async def tick(self):
        rt=self.rt;s=rt.settings
        if not enabled() or not routing.enabled() or not rt.uw or not s.flags.tracking or not s.flags.proposals or not s.flags.selection:return
        now=utcnow();in_session=within_session(now,s.market.open_et,s.market.close_et)
        # Outcome notifications remain retryable after session close/restarts.
        for row in await rt.db.fetch(PENDING):
            if row['status']!='SEARCHING':await self.notify(row)
            elif now>=row['expires_at'] or not in_session or not parent_eligible(await rt.db.fetchrow(q.GET_TRADE,row['origin_trade_id']),now):
                await rt.db.execute(SKIP,row['origin_trade_id'],row['reason'] or 'انتهت مهلة البحث دون اكتمال الزخم والسيولة والسعر.')
                await self.notify(await rt.db.fetchrow(GET,row['origin_trade_id']))
        if not in_session:return
        for parent in await rt.db.fetch(q.TRACKING_TRADES):
            if not parent_eligible(parent,utcnow()):continue
            event=routing.event_for_channel('ENTRY_CARD',routing.PRIVATE_CHAT,parent['publication_chat_id'])
            state=await rt.db.fetchrow(q.EVENT_STATUS,'trade',parent['id'],event,0)
            if not state or state['status']!='SENT':continue
            row=await rt.db.fetchrow(GET,parent['id'])
            if row and row['status']!='SEARCHING':continue
            quote,momentum=await self.momentum(parent['option_symbol'])
            if not threshold(parent,quote,utcnow()):continue
            if row is None:
                now=utcnow()
                await rt.db.execute(START,parent['id'],now,min(now+timedelta(minutes=5),et_datetime(et_date(now),s.market.close_et)))
                row=await rt.db.fetchrow(GET,parent['id'])
            if not momentum:continue
            try:await self.search(parent,row)
            except asyncio.CancelledError:raise
            except Exception as error:
                self.last_error=type(error).__name__;log.exception('Continuation parent %s failed',parent['id'])
                await rt.db.execute("UPDATE private_continuations SET reason=$2 WHERE origin_trade_id=$1 AND status='SEARCHING'",
                                    parent['id'],'تعذر التحقق من البيانات أو صلاحية النشر؛ لم يُفتح عقد جديد.')

    async def search(self,parent,row):
        rt=self.rt;s=rt.settings
        if row['status']!='SEARCHING' or utcnow()>=row['expires_at']:return
        signal=await rt.db.fetchrow(q.LATEST_DIRECTION_SIGNAL,
            utcnow()-timedelta(seconds=s.selection.momentum_max_age_seconds),utcnow(),'SPX')
        if not signal or signal['direction']!=parent['direction'] or await rt.pipeline._confirmed_flow(signal) is None:
            await rt.db.execute("UPDATE private_continuations SET reason=$2 WHERE origin_trade_id=$1 AND status='SEARCHING'",
                                parent['id'],'لم تتأكد إشارة مؤشر حديثة بنفس الاتجاه مع تدفقات السوق.')
            return
        chain=[]
        for page in range(20):
            quotes,response=await rt.uw.option_contracts('SPX',expiry=parent['expiry'],option_type=parent['direction'].lower(),page=page)
            chain.extend(quotes)
            if len(data_rows(response.data))<500:break
        else:raise DataIssue('CONTINUATION_CHAIN_INCOMPLETE')
        for candidate in ordered_candidates(chain,parent,s):
            if utcnow()>=row['expires_at']:return
            if await rt.db.fetchval(ACTIVE,candidate.occ.right,candidate.occ.strike,candidate.occ.expiry):continue
            quote,momentum=await self.momentum(candidate.symbol)
            if not momentum:continue
            flow=await self.flow(candidate.symbol)
            if not self.qualified(quote,flow,parent):continue
            # A slow chain/flow call may invalidate the parent or the candidate.
            original,good=await self.momentum(parent['option_symbol'])
            latest,good_child=await self.momentum(candidate.symbol)
            if not good or not threshold(parent,original,utcnow()):return
            if not good_child or not self.qualified(latest,flow,parent):continue
            await routing.check_permission(rt.app.bot,routing.PRIVATE_CHAT,s.signal_chat_id)
            confirmed=await rt.pipeline._confirmed_flow(signal)
            if confirmed is None:return
            child=await self.create(parent,row,latest,original,flow,signal)
            if child:
                await rt.tracker.ensure_started(child)
                await self.notify(await rt.db.fetchrow(GET,parent['id']))
            return

    async def create(self,parent,row,quote,original,flow,signal):
        rt=self.rt;s=rt.settings
        async with rt.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(q.LOCK_TRADE_DIRECTION,parent['direction'])
                await conn.execute('SELECT pg_advisory_xact_lock($1::bigint)',-7000000000000000000+parent['id'])
                current=await conn.fetchrow(q.GET_TRADE+' FOR UPDATE',parent['id'])
                attempt=await conn.fetchrow(GET+' FOR UPDATE',parent['id']);now=utcnow()
                if (not attempt or attempt['status']!='SEARCHING' or now>=attempt['expires_at']
                    or not enabled() or not routing.enabled() or not parent_eligible(current,now)
                    or not within_session(now,s.market.open_et,s.market.close_et)
                    or not threshold(current,original,now) or not self.qualified(quote,flow,parent)):
                    return None
                latest_signal=await conn.fetchrow(q.LATEST_DIRECTION_SIGNAL,
                    now-timedelta(seconds=s.selection.momentum_max_age_seconds),now,'SPX')
                if not latest_signal or latest_signal['id']!=signal['id']:return None
                if await conn.fetchval(q.COUNT_TRACKING_DIRECTION,parent['direction'])>=6:return None
                if await conn.fetchval(ACTIVE,quote.occ.right,quote.occ.strike,quote.occ.expiry):return None
                if await conn.fetchval(q.OPEN_DIRECTION_STRIKE_EXISTS,quote.occ.right,quote.occ.strike):return None
                key=f"private-continuation:{parent['id']}"
                evidence=dict(continuation_origin=parent['id'],threshold_dollars=100,indicator_signal_id=signal['id'],
                              parent_bid=str(original.bid),net_buy=str(flow.contracts[quote.symbol].net_buy),
                              flow_end=flow.end.isoformat(),spread=str(quote.spread))
                sid=await conn.fetchval(q.INSERT_SIGNAL,key,'private_continuation',parent['direction'],
                                       'CONTINUATION','SPX','5',None,now,evidence)
                if sid is None:return None
                await conn.execute("UPDATE signals SET status='PROCESSED' WHERE id=$1",sid)
                proposal=await conn.fetchrow(q.INSERT_PROPOSAL,sid,None,parent['direction'],quote.symbol,
                    quote.occ.strike,quote.occ.expiry,quote.ask,quote.bid,quote.ask,quote.last,
                    'PRIVATE_CONTINUATION',None,evidence,now+timedelta(minutes=1))
                await conn.execute("UPDATE proposals SET status='APPROVED',publication_chat_id=$2,decided_at=$3,decision_note='Private continuation experiment' WHERE id=$1",
                                   proposal['id'],routing.PRIVATE_CHAT,now)
                record=dict(quote.as_record(),**evidence)
                child=await conn.fetchrow(q.INSERT_TRADE,proposal['id'],sid,key,quote.symbol,parent['direction'],
                    quote.occ.strike,quote.occ.expiry,quote.ask,quote.ask,record,'PRIVATE_CONTINUATION',None,
                    now,now,quote.bid,quote.ask,quote.last)
                await conn.execute("UPDATE private_continuations SET status='OPENED',child_trade_id=$2,evidence=$3::jsonb WHERE origin_trade_id=$1",
                                   parent['id'],child['id'],evidence)
                return child

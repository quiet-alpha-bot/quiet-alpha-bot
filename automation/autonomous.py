"""Owner-approved opportunities from fresh contract evidence, never synthetic TV signals.

Activation is separate from existing strategies. Closed trade rows are read-only.
Exact-contract quote observations and trigger identities survive leader restarts.
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import timedelta
from decimal import Decimal

from . import queries as q
from .company_radar import (CompanyRadar, DataIssue, LATEST, INSERT, company_text,
                            data_rows, number, MIN_PREMIUM)
from .entry import within_tolerance
from .liquidity import eligible_flow
from .models import Direction, parse_ts
from .occ import parse_occ
from .selection import select_with_flow
from .timeutil import utcnow, et_date, et_datetime, within_session

log = logging.getLogger('quiet-alpha.autonomous')
GET_QUOTE = 'SELECT * FROM opportunity_quotes WHERE option_symbol=$1'
SAVE_QUOTE = '''INSERT INTO opportunity_quotes(option_symbol,bid,tape_at,observed_at)
VALUES($1,$2,$3,$4) ON CONFLICT(option_symbol) DO UPDATE SET
 previous_bid=opportunity_quotes.bid, previous_tape_at=opportunity_quotes.tape_at,
 previous_observed_at=opportunity_quotes.observed_at,
 bid=EXCLUDED.bid,tape_at=EXCLUDED.tape_at,observed_at=EXCLUDED.observed_at
WHERE EXCLUDED.observed_at >= opportunity_quotes.observed_at + interval '30 seconds'
 AND EXCLUDED.tape_at > opportunity_quotes.tape_at'''
GET_TRIGGER = '''SELECT a.* FROM autonomous_triggers a
JOIN signals s ON s.id=a.signal_db_id WHERE s.id=$1 AND s.source='autonomous' '''
INSERT_TRIGGER = '''INSERT INTO autonomous_triggers
(event_key,kind,option_symbol,origin_trade_id,alert_id,reference_entry,created_at,expires_at)
VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(event_key) DO NOTHING RETURNING id'''
LINK_TRIGGER = 'UPDATE autonomous_triggers SET signal_db_id=$2 WHERE id=$1'
REENTRY_TRADES = '''SELECT DISTINCT ON(t.option_symbol) t.* FROM trades t
WHERE t.status='CLOSED' AND t.close_reason IN ('PROTECTED_STOP','OWNER_EXIT') AND t.expiry=$1
AND closed_at >= $2 AND success_detected_at IS NOT NULL
AND highest_bid >= approved_entry+$3 AND current_bid < highest_bid
AND NOT EXISTS(SELECT 1 FROM trades newer WHERE newer.option_symbol=t.option_symbol
               AND newer.approval_time>t.closed_at)
ORDER BY t.option_symbol, t.closed_at DESC, t.id DESC LIMIT 60'''
ACTIVE = '''SELECT EXISTS(SELECT 1 FROM trades WHERE status='TRACKING'
AND direction=$1 AND strike=$2 AND expiry=$3)
OR EXISTS(SELECT 1 FROM proposals WHERE direction=$1 AND strike=$2 AND expiry=$3
AND status IN ('PENDING_APPROVAL','NEEDS_RECONFIRM','APPROVING')
AND (expires_at IS NULL OR expires_at>now()))'''
RECENT_ALERTS = '''SELECT * FROM spx_radar_alerts WHERE window_end >= $1
AND market_date=$2 ORDER BY id DESC LIMIT 60'''


def enabled(kind):
    name = 'REENTRY_WATCH_ENABLED' if kind == 'REENTRY' else 'SPX_UNUSUAL_RADAR_ENABLED'
    return os.getenv(name, 'false').lower() in ('true','1','yes','on')


def recovery_price(entry, ask, approval):
    return (ask is not None and ask >= entry
            and within_tolerance(entry, ask, approval.tolerance_abs, approval.tolerance_pct))


def fresh_quote(quote, now):
    return (quote is not None and quote.bid is not None and quote.ask is not None
            and quote.bid.is_finite() and quote.ask.is_finite()
            and 0 <= quote.bid <= quote.ask and quote.ask > 0
            and quote.last_tape_time is not None
            and 0 <= (now-quote.last_tape_time).total_seconds() <= 120)


def rising(quote, sample, now, after=None):
    if not fresh_quote(quote, now) or sample is None:
        return False
    # Compare separate tape observations, never repeated HTTP retrievals.
    prior = sample
    if quote.last_tape_time <= sample['tape_at'] or (now-sample['observed_at']).total_seconds() < 30:
        if sample['previous_bid'] is None:
            return False
        prior = dict(bid=sample['previous_bid'], tape_at=sample['previous_tape_at'],
                     observed_at=sample['previous_observed_at'])
    return (prior['observed_at'] is not None
            and 30 <= (now-prior['observed_at']).total_seconds() <= 180
            and (after is None or prior['observed_at'] >= after)
            and quote.last_tape_time > prior['tape_at'] and quote.bid > prior['bid'])


class SPXRadar(CompanyRadar):
    latest_query = LATEST.replace('company_radar_alerts','spx_radar_alerts')
    insert_query = INSERT.replace('company_radar_alerts','spx_radar_alerts')

    @staticmethod
    def render_text(*args):
        return company_text(*args).replace('رادار سيولة الشركات','رادار SPX — سيولة عقود اليوم')

    async def candidates(self, ticker, now):
        found = {}
        for page in range(20):
            response = await self.uw.get('/api/stock/SPX/option-contracts', {
                'expiry':et_date(now).isoformat(), 'exclude_zero_vol_chains':'true',
                'limit':500, 'page':page})
            rows = data_rows(response.data)
            for row in rows:
                symbol = row.get('option_symbol'); occ = parse_occ(symbol)
                if occ is None or occ.root not in ('SPX','SPXW') or occ.expiry != et_date(now):
                    raise DataIssue('SPX_EXPIRY_FILTER_MISMATCH')
                at = parse_ts(row.get('last_tape_time'))
                if (at is not None and 0 <= (now-at).total_seconds() <= 120
                    and number(row.get('total_premium')) >= MIN_PREMIUM):
                    found[symbol] = number(row['total_premium'])
            if len(rows)<500:
                return sorted(found,key=found.get,reverse=True)
        raise DataIssue('SPX_CHAIN_PAGE_LIMIT')

    async def tick(self):
        now = utcnow()
        if not enabled('SPX_RADAR') or not within_session(now,self.s.market.open_et,self.s.market.close_et):
            return
        end=now.replace(second=0,microsecond=0); start=end-timedelta(minutes=5)
        if start<et_datetime(et_date(now),self.s.market.open_et):
            return
        if self._day!=et_date(now):
            self._day=et_date(now); self._history.clear()
        self.last_check_at=now; self.last_error=None
        self.scanned=self.insufficient_history=0
        symbols=await self.candidates('SPX',now)
        for symbol in self.scan_batch('SPX', symbols):
            current = utcnow()
            if not within_session(current,self.s.market.open_et,self.s.market.close_et):
                return
            end=current.replace(second=0,microsecond=0); start=end-timedelta(minutes=5)
            self.mark_attempt('SPX', symbol)
            try:
                await self.check(symbol,start,end); self.scanned+=1
            except DataIssue as error:
                if str(error) in ('INSUFFICIENT_HISTORY','INCOMPLETE_HISTORY'):
                    self.insufficient_history+=1
                else:
                    self.last_error=str(error)

    async def send(self,row):
        await self.publisher.text_once('spx_radar',row['id'],'SPX_UNUSUAL_FLOW',
                                      row['message_text'],payload=row['payload'],max_attempts=3)


class AutonomousOpportunities:
    def __init__(self,settings,db,uw,liquidity,pipeline,schedule):
        self.s,self.db,self.uw,self.liquidity=settings,db,uw,liquidity
        self.pipeline,self.schedule=pipeline,schedule
        self.last_error=None; self.last_check_at=None

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error=getattr(error,'kind',type(error).__name__)
                log.exception('Autonomous opportunity scan failed')
            await asyncio.sleep(60)

    async def sample(self,quote,now):
        if fresh_quote(quote,now):
            await self.db.execute(SAVE_QUOTE,quote.symbol,quote.bid,quote.last_tape_time,now)

    async def flow(self,symbol,now):
        start=max(now-timedelta(minutes=5),et_datetime(et_date(now),self.s.market.open_et))
        snapshot=await self.liquidity.snapshot(start,now,single_leg=True,symbols=[symbol])
        if not snapshot.complete or not snapshot.fresh(utcnow()):
            self.last_error=snapshot.reason or 'STALE_CONTRACT_FLOW'
            return None
        if not eligible_flow(snapshot.contracts.get(symbol),utcnow()):
            return None
        return snapshot

    async def quote_and_momentum(self,symbol,after=None):
        quotes,_=await self.uw.quotes_for([symbol]); quote=quotes.get(symbol); now=utcnow()
        if quote is not None and quote.symbol != symbol:
            raise DataIssue("QUOTE_CONTRACT_MISMATCH")
        sample=await self.db.fetchrow(GET_QUOTE,symbol)
        valid=rising(quote,sample,now,after)
        await self.sample(quote,now)
        return quote if valid else None

    async def qualified(self,quote):
        now=utcnow(); occ=quote.occ
        if occ is None or occ.expiry!=et_date(now) or occ.root!=self.s.uw.contract_root:
            return None
        if await self.db.fetchval(ACTIVE,occ.right,occ.strike,occ.expiry):
            return None
        snapshot=await self.flow(quote.symbol,now)
        if snapshot is None:
            return None
        selected=select_with_flow([quote],Direction(occ.right),occ.expiry,
            self.s.uw.contract_root,self.s.selection,utcnow(),snapshot)
        return snapshot if selected.selected is not None else None

    async def tick(self):
        now=utcnow()
        if not within_session(now,self.s.market.open_et,self.s.market.close_et):
            return
        self.last_check_at=now; self.last_error=None
        if enabled('REENTRY'):
            trades=await self.db.fetch(REENTRY_TRADES,et_date(now),
                et_datetime(et_date(now),self.s.market.open_et),self.s.tracking.success_premium_gain)
            for trade in trades:
                quote=await self.quote_and_momentum(trade['option_symbol'],trade['closed_at'])
                if (quote is None
                    or not recovery_price(trade['approved_entry'],quote.ask,self.s.approval)):
                    continue
                if await self.qualified(quote) is not None:
                    await self.emit('REENTRY',f"reentry:{trade['id']}",quote,
                                    trade_id=trade['id'],entry=trade['approved_entry'])
        if enabled('SPX_RADAR'):
            alerts=await self.db.fetch(RECENT_ALERTS,utcnow()-timedelta(seconds=120),et_date(utcnow()))
            for alert in alerts:
                await self.radar_opportunity(alert)

    async def radar_opportunity(self,alert):
        if not 0 <= (utcnow()-alert['window_end']).total_seconds() <= 120:
            return
        original=parse_occ(alert['option_symbol'])
        if original is None or original.root!=self.s.uw.contract_root:
            return  # SPX regular contracts may be reported; selection uses configured root.
        blocked=await self.db.fetchval(ACTIVE,original.right,original.strike,original.expiry)
        symbols=[alert['option_symbol']]
        if blocked:
            chain=[]
            for page in range(20):
                quotes,response=await self.uw.option_contracts('SPX',expiry=original.expiry,
                    option_type=original.right.lower(),page=page)
                chain.extend(quotes)
                if len(data_rows(response.data))<500:
                    break
            else:
                raise DataIssue('SPX_ALTERNATIVE_PAGE_LIMIT')
            # Only immediate listed neighbors on either side, never a remote strike.
            strikes=sorted({v.occ.strike for v in chain if v.occ and v.occ.expiry==original.expiry
                            and v.occ.root==original.root and v.occ.right==original.right})
            below=[x for x in strikes if x<original.strike]; above=[x for x in strikes if x>original.strike]
            neighbors={x for x in below[-1:]+above[:1] if abs(x-original.strike)<=10}
            symbols=[v.symbol for v in chain if v.occ and v.occ.strike in neighbors
                     and v.occ.root==original.root and v.occ.right==original.right
                     and v.occ.expiry==original.expiry]
        candidates=[]
        for symbol in symbols:
            quote=await self.quote_and_momentum(symbol)
            if quote is None:
                continue
            snapshot=await self.qualified(quote)
            if snapshot is not None:
                candidates.append((snapshot.contracts[symbol].net_buy,quote))
        if candidates and 0 <= (utcnow()-alert['window_end']).total_seconds() <= 120:
            _,quote=max(candidates,key=lambda item:item[0])
            await self.emit('SPX_RADAR',f"spx-radar:{alert['id']}",quote,alert_id=alert['id'])

    async def emit(self,kind,event_key,quote,trade_id=None,entry=None,alert_id=None):
        now=utcnow()
        if (not enabled(kind) or not self.s.flags.selection or not self.s.flags.proposals
            or not fresh_quote(quote,now)
            or not within_session(now,self.s.market.open_et,self.s.market.close_et)):
            return
        # Insert durable evidence and signal together. Restart cannot reissue an event.
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                # Serialize both radar and reentry proposals for the same direction/strike.
                await conn.execute('SELECT pg_advisory_xact_lock(hashtext($1))',
                                   f'autonomous:{quote.occ.right}:{quote.occ.strike}')
                if await conn.fetchval(ACTIVE,quote.occ.right,quote.occ.strike,quote.occ.expiry):
                    return
                busy=await conn.fetchval('''SELECT 1 FROM autonomous_triggers WHERE option_symbol=$1
                    AND expires_at>$2 AND signal_db_id IS NOT NULL LIMIT 1''',quote.symbol,now)
                if busy:
                    return
                trigger=await conn.fetchval(INSERT_TRIGGER,event_key,kind,quote.symbol,trade_id,
                    alert_id,entry,now,now+timedelta(seconds=300))
                if trigger is None:
                    return
                signal=await conn.fetchval(q.INSERT_SIGNAL,f'autonomous:{trigger}','autonomous',
                    quote.occ.right,kind,'SPX','5',None,now,{'trigger_id':trigger})
                await conn.execute(LINK_TRIGGER,trigger,signal)
        await self.schedule(signal)

    async def accepts_quote(self,row,quote):
        trigger=await self.db.fetchrow(GET_TRIGGER,row['id'])
        now=utcnow()
        if (trigger is None or not enabled(trigger['kind'])
            or not trigger['created_at']<=now<trigger['expires_at']
            or not fresh_quote(quote,now) or quote.symbol!=trigger['option_symbol']):
            return False
        return (trigger['kind']!='REENTRY' or
                recovery_price(trigger['reference_entry'],quote.ask,self.s.approval))

    async def validate(self,row):
        """Used before proposal AND approval. Never trust webhook payload alone."""
        trigger=await self.db.fetchrow(GET_TRIGGER,row['id'])
        now=utcnow()
        if (trigger is None or not enabled(trigger['kind']) or not trigger['created_at']<=now<trigger['expires_at']
            or not within_session(now,self.s.market.open_et,self.s.market.close_et)):
            return None
        occ=parse_occ(trigger['option_symbol'])
        if occ is None or occ.expiry!=et_date(now) or occ.right!=row['direction']:
            return None
        after=None
        if trigger['kind']=='REENTRY':
            trade=await self.db.fetchrow(q.GET_TRADE,trigger['origin_trade_id'])
            if (trade is None or trade['status']!='CLOSED' or trade['close_reason'] not in ('PROTECTED_STOP','OWNER_EXIT')
                or trade['success_detected_at'] is None):
                return None
            after=trade['closed_at']
        quote=await self.quote_and_momentum(trigger['option_symbol'],after)
        if quote is None:
            return None
        if trigger['kind']=='REENTRY' and not recovery_price(trigger['reference_entry'],quote.ask,self.s.approval):
            return None
        snapshot=await self.flow(quote.symbol,utcnow())
        if (utcnow()>=trigger['expires_at'] or not fresh_quote(quote,utcnow())
            or not within_session(utcnow(),self.s.market.open_et,self.s.market.close_et)):
            return None
        return snapshot

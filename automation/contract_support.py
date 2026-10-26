"""One evidence-based support alert per successful open trade; no order actions."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from decimal import Decimal
from html import escape

from . import queries as q
from .liquidity import eligible_flow
from .pipeline import _payload_strength
from .timeutil import utcnow, within_session, et_date
from .telegram_ui import contract_label

logger = logging.getLogger('quiet-alpha.contract-support')
POLL_SECONDS = 60
FLOW_MINUTES = 5
MIN_STRENGTH = Decimal('65')
MAX_AGE = 120
EVENT = 'CONTRACT_SUPPORT'


def supported_trade(trade, now, gain):
    if (trade is None or trade['status'] != 'TRACKING'
        or trade['success_detected_at'] is None or not trade['success_sent']
        or trade['expiry'] != et_date(now)):
        return False
    bid = trade['current_bid']
    entry = Decimal(trade['approved_entry'])
    return (bid is not None and bid >= entry + gain
            and all(trade.get(key) is not None
                    and 0 <= (now - trade[key]).total_seconds() <= MAX_AGE
                    for key in ('last_poll_at', 'last_tape_time')))


def confirmed_momentum(signal, direction, now, max_age):
    if signal is None or signal['direction'] != direction:
        return None
    age = (now - signal['signal_time']).total_seconds()
    strength = _payload_strength(signal)
    return strength if 0 <= age <= max_age and strength is not None and strength >= MIN_STRENGTH else None


def support_text(trade, flow, strength):
    entry, bid = Decimal(trade['approved_entry']), Decimal(trade['current_bid'])
    profit = (bid-entry)*100
    label = contract_label(Decimal(trade['strike']),trade['direction'])
    return (
        '🦋 <b>QUIET ALPHA — دعم السيولة والزخم</b>\n\n'
        f"{'🟢' if trade['direction']=='CALL' else '🔴'} <b>{escape(label)}</b>\n"
        f'سعر الدخول: <b>${entry:.2f}</b> · الحالي: <b>${bid:.2f}</b>\n'
        f'العائد الحالي للعقد: <b>+${profit:.2f}</b>\n\n'
        f'السيولة: صافي تدفقات شراء <b>${flow.net_buy:,.2f}</b> خلال آخر {FLOW_MINUTES} دقائق.\n'
        f'الزخم: المؤشر متوافق مع اتجاه العقد بقوة <b>{strength:.0f}%</b>.\n\n'
        'تدفقات الشراء متجددة على العقد نفسه، والزخم يدعم اتجاهه حاليًا. '
        'تستمر المتابعة مع الوقف المعتمد.'
    )


class ContractSupport:
    def __init__(self, settings, db, publisher, liquidity):
        self.s, self.db, self.publisher, self.liquidity = settings, db, publisher, liquidity
        self.last_error = None
        self.last_check_at = None

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception('Contract support check failed')
            await asyncio.sleep(POLL_SECONDS)

    async def tick(self):
        now = utcnow()
        if not within_session(now,self.s.market.open_et,self.s.market.close_et):
            return
        self.last_check_at = now
        self.last_error = None
        for trade in await self.db.fetch(q.TRACKING_TRADES):
            if not supported_trade(trade,now,self.s.tracking.success_premium_gain):
                continue
            event = await self.db.fetchrow(q.EVENT_STATUS,'trade',trade['id'],EVENT,0)
            if event and event['status'] != 'FAILED':
                continue
            try:
                await self.check(trade)
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception('Contract support check failed for trade #%s',trade['id'])

    async def check(self, trade):
        now = utcnow()
        signal = await self.db.fetchrow(q.LATEST_DIRECTION_SIGNAL,
            now-timedelta(seconds=self.s.selection.momentum_max_age_seconds),now,self.s.uw.spot_ticker)
        if confirmed_momentum(signal,trade['direction'],now,self.s.selection.momentum_max_age_seconds) is None:
            return
        # Query the locked contract itself, even after it trades above $4.50.
        # Entry candidate price limits must not hide a successful contract.
        end = now.replace(second=0,microsecond=0)
        snapshot = await self.liquidity.snapshot(end-timedelta(minutes=FLOW_MINUTES),end,
                                                  single_leg=True,symbols=[trade['option_symbol']])
        if not snapshot.complete:
            self.last_error = snapshot.reason or 'INCOMPLETE_FLOW'
            return
        now = utcnow()
        # Tracking continues independently during UW requests. Re-read both
        # trade and indicator so a stale pre-request decision cannot be used.
        latest = await self.db.fetchrow(q.GET_TRADE,trade['id'])
        if (not within_session(now,self.s.market.open_et,self.s.market.close_et)
            or not supported_trade(latest,now,self.s.tracking.success_premium_gain)):
            return
        signal = await self.db.fetchrow(q.LATEST_DIRECTION_SIGNAL,
            now-timedelta(seconds=self.s.selection.momentum_max_age_seconds),now,self.s.uw.spot_ticker)
        strength = confirmed_momentum(signal,latest['direction'],now,self.s.selection.momentum_max_age_seconds)
        flow = snapshot.contracts.get(latest['option_symbol'])
        if (strength is None or not snapshot.fresh(now) or not eligible_flow(flow,now)
            or flow.last_at < latest['success_detected_at']):
            return
        await self.publisher.text_once('trade',latest['id'],EVENT,support_text(latest,flow,strength),
            payload={'symbol':latest['option_symbol'],'window_start':snapshot.start.isoformat(),
                     'window_end':snapshot.end.isoformat(),'net_buy':str(flow.net_buy),
                     'indicator_strength':str(strength),'bid':str(latest['current_bid'])},
            max_attempts=1_000_000)

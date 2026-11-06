"""Private-only Friday report, after confirmed daily report AND closing greeting."""
import asyncio
import logging
import os
from datetime import timedelta
from . import queries as q, channel_routing as routing
from .report_sections import weekly_text, send_pages
from .timeutil import utcnow, et_date, et_datetime

log=logging.getLogger('quiet-alpha.weekly-report')

def enabled():
    return os.getenv('PRIVATE_WEEKLY_REPORT_ENABLED','true').lower() in ('true','1','yes','on')

DAILY_SENT="""SELECT EXISTS(SELECT 1 FROM events WHERE entity_type='daily' AND entity_id=$1
AND event_type=$2 AND status='SENT')"""
CLOSING_SENT="""SELECT EXISTS(SELECT 1 FROM closing_messages c JOIN events e
ON e.entity_type='closing_message' AND e.entity_id=c.id AND e.event_type='PRIVATE'
AND e.status='SENT' WHERE c.market_date=$1 AND c.private_chat_id=$2)"""

class WeeklyReport:
    def __init__(self,rt):self.rt=rt; self.last_error=None

    async def run(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception as error:
                self.last_error=type(error).__name__;log.exception('Weekly report failed')
            await asyncio.sleep(30)

    async def tick(self):
        rt=self.rt
        if not enabled() or not routing.enabled():return
        now=utcnow();day=et_date(now)
        # Friday plus weekend catch-up after a restart; never report next week's trades.
        if day.weekday()<4:return
        friday=day-timedelta(days=day.weekday()-4)
        if now<et_datetime(friday,rt.settings.market.close_et)+timedelta(minutes=2):return
        ident=int(friday.strftime('%Y%m%d'));chat=routing.PRIVATE_CHAT
        if not await rt.db.fetchval(DAILY_SENT,ident,'DAILY_REPORT_'+chat):return
        closed=await rt.db.fetchval(CLOSING_SENT,friday,chat)
        if not closed:
            closed=await rt.db.fetchval(DAILY_SENT,ident,'FINISH_GREETING_'+chat)
        if not closed:return
        trades=[]
        for offset in range(4,-1,-1):
            rows=await rt.db.fetch(q.DAILY_TRADES,friday-timedelta(days=offset))
            trades.extend(routing.report_groups(rows).get(chat,[]))
        if any(t['status']=='TRACKING' for t in trades):return
        text,payload=weekly_text(trades,friday,rt.settings.tracking.success_premium_gain)
        if await send_pages(rt,'weekly_report',ident,'PRIVATE_WEEKLY',text,chat,payload):
            await rt.publisher.text_once('weekly_report',ident,'ADMIN_RESULT',
                '✅ نُشر البيان الأسبوعي التجريبي في الخاصة بعد البيان اليومي ورسالة الختام.')

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
        # Weekday execution is owned by the daily orchestrator. On weekends,
        # finish any interrupted Friday sequence without crossing channel scope.
        if day.weekday()==4:return
        from .session_finish import requested_channels
        finished=await requested_channels(rt.db,friday)
        rows=await rt.db.fetch(q.DAILY_TRADES,friday)
        groups=routing.report_groups(rows)
        from .channel_wrapup import send as send_wrapup
        for chat in (routing.PUBLIC_CHAT,routing.PRIVATE_CHAT):
            group=groups.get(chat,[]);ids={t['id'] for t in group}
            await send_wrapup(rt,friday,chat,group,[t for t in rows if t['id'] in ids],manual=chat in finished)

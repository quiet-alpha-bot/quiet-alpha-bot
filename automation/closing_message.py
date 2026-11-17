"""Owner-selected closing greeting, delivered only after each channel's report."""
import asyncio
import logging
import os
from datetime import time, timedelta

from .session_finish import requested_channels
from . import queries as q
from . import channel_routing as routing
from .opening_prayer import keyboard, destinations
from .timeutil import utcnow, et_date, et_datetime

log=logging.getLogger('quiet-alpha.closing-message')
CAPTION=('<b>لكل جلسة حكاية، ولكل يوم فرصة 🦋</b>\n'
         'الحمد لله دائماً وأبداً، وشكراً لثقتكم ووجودكم معنا 🤍\n'
         'دمتم بخير وفي أمان الله 🕊️')
INSERT='''INSERT INTO closing_messages(market_date,caption,public_chat_id,private_chat_id,expires_at)
VALUES($1,$2,$3,$4,$5) ON CONFLICT(market_date) DO NOTHING'''
GET_DAY='SELECT * FROM closing_messages WHERE market_date=$1'
GET='SELECT * FROM closing_messages WHERE id=$1'
DECIDE='''UPDATE closing_messages SET status=$2,decided_by=$3,decided_at=$4,publication_choice=$5
WHERE id=$1 AND status='PENDING' RETURNING *'''
EXPIRE="UPDATE closing_messages SET status='EXPIRED' WHERE expires_at<=$1 AND status='PENDING'"


def enabled():
    return os.getenv('CLOSING_MESSAGE_ENABLED','false').lower() in ('true','1','yes','on')


class ClosingMessage:
    def __init__(self,runtime):
        self.rt=runtime
        self.last_error=None

    async def report_sent(self,day,chat):
        event=await self.rt.db.fetchrow(q.EVENT_STATUS,'daily',int(day.strftime('%Y%m%d')),
                                        'DAILY_REPORT_'+str(chat),0)
        return bool(event and event['status']=='SENT')

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error=type(error).__name__
                log.exception('Closing message cycle failed')
            await asyncio.sleep(15)

    async def tick(self):
        # Ordered channel_wrapup is the only automatic closing publisher.
        return

    async def publish(self,row):
        finished=await requested_channels(self.rt.db,row["market_date"])
        if not enabled() or row['status']!='APPROVED':return
        states=[]
        for event,chat in destinations(row):
            if chat in finished:continue
            if await self.report_sent(row['market_date'],chat):
                await self.rt.publisher.text_once('closing_message',row['id'],event,
                    row['caption'],chat_id=chat)
            state=await self.rt.db.fetchrow(q.EVENT_STATUS,'closing_message',row['id'],event,0)
            states.append(state['status'] if state else 'PENDING')
        if states and all(state=='SENT' for state in states):
            await self.rt.publisher.text_once('closing_message',row['id'],'RESULT',
                '✅ نُشرت رسالة الختام في الوجهة المختارة.')

    async def callback(self,update,context):
        query=update.callback_query
        if query is None:return
        try:
            await self._callback(query)
        except Exception as error:
            self.last_error=type(error).__name__
            log.exception('Closing message approval failed')
            try:await query.answer('تعذر إكمال الطلب؛ راجعي حالة البوت قبل المحاولة مجددًا.',show_alert=True)
            except Exception:pass

    async def _callback(self,query):
        if query.from_user is None or query.from_user.id!=self.rt.settings.owner_user_id:
            await query.answer('غير مصرح.',show_alert=True);return
        await query.answer('أصبح الختام تلقائيًا بعد البيان؛ هذه المعاينة قديمة.',show_alert=True)

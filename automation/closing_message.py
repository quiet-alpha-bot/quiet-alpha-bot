"""Owner-selected closing greeting, delivered only after each channel's report."""
import asyncio
import logging
import os
from datetime import time, timedelta

from .session_finish import stopped
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
DECIDE='''UPDATE closing_messages SET status=$2,decided_by=$3,decided_at=now(),publication_choice=$5
WHERE id=$1 AND status='PENDING' AND expires_at>$4 RETURNING *'''
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
        if not enabled():return
        now=utcnow();day=et_date(now);rt=self.rt
        if await stopped(rt.db,day):return
        await rt.db.execute(EXPIRE,now)
        if day.weekday()>=5 or now<et_datetime(day,rt.settings.market.close_et):return
        if rt.settings.owner_user_id is None or not routing.enabled():
            self.last_error='OWNER_OR_CHANNEL_ROUTING_MISSING';return
        row=await rt.db.fetchrow(GET_DAY,day)
        if row is None:
            if not (await self.report_sent(day,routing.PUBLIC_CHAT) or
                    await self.report_sent(day,routing.PRIVATE_CHAT)):return
            await rt.db.execute(INSERT,day,CAPTION,routing.PUBLIC_CHAT,routing.PRIVATE_CHAT,
                                et_datetime(day+timedelta(days=1),time(0)))
            row=await rt.db.fetchrow(GET_DAY,day)
        if row['status']=='PENDING':
            await rt.publisher.text_once('closing_message',row['id'],'PREVIEW',
                row['caption']+'\n\nمعاينة الإدارة — اختاري مكان النشر أو تخطي اليوم.\n'
                'كل قناة تستقبل الختام بعد وصول بيانها اليومي فقط.\n'
                'إذا لم يصل بيان إحدى القنوات، ينتظر إرسال ختامها حتى نهاية اليوم بتوقيت نيويورك.',
                reply_markup=keyboard(row['id'],'qac'))
        elif row['status']=='APPROVED':
            await self.publish(row)

    async def publish(self,row):
        if await stopped(self.rt.db,row["market_date"]):return
        if not enabled() or row['status']!='APPROVED' or utcnow()>=row['expires_at']:return
        states=[]
        for event,chat in destinations(row):
            if await self.report_sent(row['market_date'],chat):
                # Recheck time after DB I/O; never send yesterday's closing today.
                if utcnow()>=row['expires_at']:return
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
        rt=self.rt
        if (query.from_user is None or rt.settings.owner_user_id is None or
            query.from_user.id!=rt.settings.owner_user_id):
            await query.answer('⛔ غير مصرح',show_alert=True);return
        if not rt.ready or not enabled() or not routing.enabled():
            await query.answer('رسالة الختام غير جاهزة حاليًا.',show_alert=True);return
        if query.message is None or str(query.message.chat.id)!=str(rt.settings.signal_chat_id):
            await query.answer('استخدمي معاينة قناة الإدارة.',show_alert=True);return
        try:
            prefix,action,ident=query.data.split(':');ident=int(ident)
            if prefix!='qac' or action not in ('public','private','both','skip'):raise ValueError
        except (ValueError,TypeError):
            await query.answer('طلب غير صالح.',show_alert=True);return
        row=await rt.db.fetchrow(GET,ident)
        if row is None or utcnow()>=row['expires_at'] or row['market_date']!=et_date(utcnow()):
            await query.answer('انتهت مهلة رسالة هذا اليوم.',show_alert=True);return
        row=await rt.db.fetchrow(DECIDE,ident,'SKIPPED' if action=='skip' else 'APPROVED',
                                query.from_user.id,utcnow(),'both' if action=='skip' else action)
        if row is None:
            await query.answer('تم تسجيل القرار مسبقًا أو انتهت المهلة.',show_alert=True);return
        await query.answer('تم تخطي اليوم.' if action=='skip' else
                           'سُجل اختيارك؛ تُرسل الرسالة بعد وصول بيان كل قناة مختارة.')
        try:await query.edit_message_reply_markup(reply_markup=None)
        except Exception:log.info('Closing preview buttons unavailable')
        if action!='skip':await self.publish(row)

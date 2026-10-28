"""Prepare at 16:15 Riyadh; only an explicit owner approval publishes photos.

Independent leader task: the radar never waits for approval or Telegram here.
Existing event ledger prevents duplicate or uncertain-delivery retries.
"""
import asyncio
import hashlib
import logging
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from . import queries as q
from . import channel_routing as routing
from .cards import PHOTO_TIMEOUTS
from .timeutil import utcnow, et_date, et_datetime, display_time

log=logging.getLogger('quiet-alpha.opening-prayer')
RIYADH=ZoneInfo('Asia/Riyadh')
ASSET=Path(__file__).resolve().parent.parent/'market_dua.jpeg'
CAPTION='<b>بسم الله نبدأ جلستنا 🦋</b>\nنسأل الله التوفيق لنا ولكم 🤍'
INSERT='''INSERT INTO opening_prayers(market_date,caption,asset_sha256,public_chat_id,private_chat_id,expires_at)
VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(market_date) DO NOTHING'''
GET_DAY='SELECT * FROM opening_prayers WHERE market_date=$1'
GET='SELECT * FROM opening_prayers WHERE id=$1'
DECIDE='''UPDATE opening_prayers SET status=$2,decided_by=$3,decided_at=now()
WHERE id=$1 AND status='PENDING' AND expires_at>$4 RETURNING *'''
EXPIRE="UPDATE opening_prayers SET status='EXPIRED' WHERE expires_at<=$1 AND status='PENDING'"


def enabled():
    return os.getenv('OPENING_PRAYER_ENABLED','false').lower() in ('true','1','yes','on')


def window(now,market):
    day=et_date(now)
    if day.weekday()>=5:
        return None
    due=datetime.combine(day,time(16,15),tzinfo=RIYADH)
    end=et_datetime(day,market.open_et)
    return (day,end) if due<=now<end else None


def keyboard(ident):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ نشر في العامة والخاصة',callback_data=f'qad:publish:{ident}')],
        [InlineKeyboardButton('❌ تخطي اليوم',callback_data=f'qad:skip:{ident}')]])


class OpeningPrayer:
    def __init__(self,runtime):
        self.rt=runtime
        self.last_error=None

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error=type(error).__name__
                log.exception('Opening prayer cycle failed')
            await asyncio.sleep(15)

    async def tick(self):
        if not enabled():
            return
        now=utcnow();rt=self.rt
        await rt.db.execute(EXPIRE,now)
        active=window(now,rt.settings.market)
        if active is None:
            return
        day,end=active
        if rt.settings.owner_user_id is None or not routing.enabled():
            self.last_error='OWNER_OR_CHANNEL_ROUTING_MISSING'
            return
        content=ASSET.read_bytes()
        await rt.db.execute(INSERT,day,CAPTION,hashlib.sha256(content).hexdigest(),
                            routing.PUBLIC_CHAT,routing.PRIVATE_CHAT,end)
        row=await rt.db.fetchrow(GET_DAY,day)
        if row['status']=='PENDING':
            await self.photo(row,'PREVIEW',rt.settings.signal_chat_id,preview=True)
        elif row['status']=='APPROVED':
            await self.publish(row)

    async def photo(self,row,event,chat,preview=False):
        if utcnow()>=row['expires_at']:
            return
        content=ASSET.read_bytes()
        if hashlib.sha256(content).hexdigest()!=row['asset_sha256']:
            self.last_error='APPROVED_ASSET_CHANGED'
            return
        caption=row['caption']
        if preview:
            caption+=('\n\nمعاينة الإدارة — لن تُنشر دون موافقتك.'
                      f"\nالموافقة متاحة حتى افتتاح السوق {display_time(row['expires_at'],'Asia/Riyadh')}.")
        async def send():
            from io import BytesIO
            with BytesIO(content) as photo:
                photo.name='market_dua.jpeg'
                return await self.rt.app.bot.send_photo(chat_id=chat,photo=photo,caption=caption,
                    parse_mode='HTML',reply_markup=keyboard(row['id']) if preview else None,**PHOTO_TIMEOUTS)
        await self.rt.publisher._deliver('opening_prayer',row['id'],event,0,
            {'publication_chat_id':str(chat),'asset_sha256':row['asset_sha256']},send,max_attempts=3)

    async def publish(self,row):
        if not enabled() or row['status']!='APPROVED' or utcnow()>=row['expires_at']:
            return
        for event,chat in [('PUBLIC',row['public_chat_id']),('PRIVATE',row['private_chat_id'])]:
            await self.photo(row,event,chat)

    async def callback(self,update,context):
        try:
            await self._callback(update,context)
        except Exception as error:
            self.last_error=type(error).__name__
            log.exception('Opening prayer approval failed')
            if update.callback_query is not None:
                try:
                    await update.callback_query.answer('تعذر إكمال الطلب؛ راجعي حالة البوت قبل المحاولة مجددًا.',show_alert=True)
                except Exception:
                    pass

    async def _callback(self,update,context):
        query=update.callback_query
        if query is None:
            return
        rt=self.rt;user=query.from_user
        if user is None or rt.settings.owner_user_id is None or user.id!=rt.settings.owner_user_id:
            await query.answer('⛔ غير مصرح',show_alert=True);return
        if not rt.ready or not enabled():
            await query.answer('بطاقة الافتتاح غير مفعّلة حاليًا.',show_alert=True);return
        if query.message is None or str(query.message.chat.id)!=str(rt.settings.signal_chat_id):
            await query.answer('استخدمي معاينة قناة الإدارة.',show_alert=True);return
        try:
            prefix,action,ident=query.data.split(':')
            if prefix!='qad' or action not in ('publish','skip'):
                raise ValueError
            ident=int(ident)
        except (ValueError,TypeError):
            await query.answer('طلب غير صالح.',show_alert=True);return
        row=await rt.db.fetchrow(GET,ident)
        if row is None or utcnow()>=row['expires_at'] or row['market_date']!=et_date(utcnow()):
            await query.answer('انتهت مهلة بطاقة اليوم.',show_alert=True);return
        if row['status']!='PENDING':
            await query.answer('تم تسجيل القرار مسبقًا.',show_alert=True);return
        # A changed asset must never be published on approval of an older preview.
        if action=='publish' and hashlib.sha256(ASSET.read_bytes()).hexdigest()!=row['asset_sha256']:
            await query.answer('تغيّرت صورة البطاقة؛ لم يتم النشر.',show_alert=True);return
        row=await rt.db.fetchrow(DECIDE,ident,'APPROVED' if action=='publish' else 'SKIPPED',user.id,utcnow())
        if row is None:
            await query.answer('تم تسجيل القرار أو انتهت المهلة.',show_alert=True);return
        await query.answer('تمت الموافقة، جارٍ النشر.' if action=='publish' else 'تم تخطي اليوم.')
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            log.info('Prayer preview buttons already removed or unavailable')
        if action=='publish':
            await self.publish(row)
            states=[]
            for event in ('PUBLIC','PRIVATE'):
                state=await rt.db.fetchrow(q.EVENT_STATUS,'opening_prayer',ident,event,0)
                states.append(state['status'] if state else 'PENDING')
            if states==['SENT','SENT']:
                await rt.publisher.text_once('opening_prayer',ident,'RESULT','✅ نُشرت بطاقة الدعاء في العامة والخاصة.')
            else:
                await rt.publisher.text_once('opening_prayer',ident,'RESULT_PENDING',
                    'سُجلت موافقتك، لكن لم يتأكد النشر في القناتين بعد. لن نكرر أي إرسال غير محسوم.')

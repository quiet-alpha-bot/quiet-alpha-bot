"""Durable, day-scoped entry pause; open trades keep tracking until final delivery."""
import logging
from datetime import date
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from . import queries as q, channel_routing as routing
from .timeutil import utcnow, et_date
from .daily_report import daily_report_text

log = logging.getLogger('quiet-alpha.session-finish')
GET = 'SELECT * FROM session_finishes WHERE market_date=$1'
EXISTS = 'SELECT EXISTS(SELECT 1 FROM session_finishes WHERE market_date=$1)'
INSERT = '''INSERT INTO session_finishes(market_date,publication_choice,requested_by)
VALUES($1,$2,$3) ON CONFLICT(market_date) DO NOTHING'''
CAPTION = ('<b>نكتفي بهذا القدر اليوم 🦋</b>\n'
           'الحمد لله دائماً وأبداً، وشكراً لثقتكم ووجودكم معنا 🤍\n'
           'دمتم بخير وفي أمان الله 🕊️')

async def stopped(db, day=None):
    return bool(await db.fetchval(EXISTS, day or et_date(utcnow())))

async def require_open(db):
    if await stopped(db):
        raise ValueError('تم الاكتفاء لليوم؛ الدخول التلقائي متوقف وتستمر متابعة العقود المفتوحة.')

class SessionFinish:
    def __init__(self, runtime):
        self.rt = runtime

    async def command(self, update, context):
        if not await self.rt._guard(update):return
        if not routing.enabled():
            await update.message.reply_text('اختيار القنوات غير مفعّل.');return
        day=et_date(utcnow())
        row=await self.rt.db.fetchrow(GET,day)
        if row:
            await update.message.reply_text('الاكتفاء مسجل لليوم؛ ننتظر إغلاق العقود وبطاقاتها ثم البيان والختام.');return
        markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(label,callback_data=f'qaf:{day.isoformat()}:{choice}')
            for label,choice in [('🌐 العامة','public'),('🔒 الخاصة','private'),('📣 الاثنتان','both'),('إلغاء','cancel')]
        ]])
        await update.message.reply_text('الاكتفاء يوقف فرص الدخول التلقائية اليوم ويُبقي تحديثات العقود المفتوحة.\n'
            'اختاري وجهة البيان والختام بعد إغلاق آخر صفقة. يبدأ الدخول مجددًا في جلسة يوم جديد.',reply_markup=markup)

    async def callback(self,update,context):
        query=update.callback_query
        if query is None:return
        rt=self.rt
        if query.from_user is None or query.from_user.id!=rt.settings.owner_user_id:
            await query.answer('⛔ غير مصرح',show_alert=True);return
        if not rt.ready or query.message is None:
            await query.answer('البوت غير جاهز.',show_alert=True);return
        if str(query.message.chat.id) not in (str(rt.settings.owner_user_id),str(rt.settings.signal_chat_id)):
            await query.answer('استخدمي خاص البوت أو الإدارة.',show_alert=True);return
        try:
            prefix,day,choice=query.data.split(':');day=date.fromisoformat(day)
            if prefix!='qaf' or day!=et_date(utcnow()):raise ValueError('هذا الاختيار ليس لجلسة اليوم.')
            if choice=='cancel':
                await query.answer('أُلغي الطلب.');await query.edit_message_reply_markup(reply_markup=None);return
            target=routing.destination(choice)
            if not routing.enabled():raise ValueError('اختيار القنوات غير مفعّل.')
            await routing.check_permission(rt.app.bot,target,rt.settings.signal_chat_id)
            async with rt.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Same locks as both automatic and manual entry commits.
                    for direction in ('CALL','PUT'):
                        await conn.execute(q.LOCK_TRADE_DIRECTION,direction)
                    if day!=et_date(utcnow()):raise ValueError('انتهت الجلسة؛ أرسلي الأمر من جديد.')
                    await conn.execute(INSERT,day,choice,query.from_user.id)
            await query.answer('تم الاكتفاء لليوم.')
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text('✅ توقف الدخول التلقائي لليوم؛ اليدوي يبقى متاحًا. تستمر تحديثات العقود المفتوحة؛ '
                'وبعد آخر إغلاق وبطاقاته نرسل البيان والختام في الوجهة المحفوظة.')
            await self.tick()
        except ValueError as error:
            await query.message.reply_text(str(error))
        except Exception:
            log.exception('Session finish failed')
            await query.message.reply_text('تعذر إكمال الطلب؛ راجعي /qa_status قبل المحاولة مجددًا.')

    async def cards_sent(self,trades):
        for trade in trades:
            if trade['status']=='TRACKING':return False
            reason=trade.get('close_reason')
            expected=[]
            if reason in ('OWNER_EXIT','OWNER_EXIT_EXCLUDED','OWNER_EXIT_FLAT'):expected.append('TRADE_EXIT')
            if reason=='BREAKEVEN_EXIT':expected.append('BREAKEVEN_EXIT')
            elif reason in ('STOP_LOSS_30','STOP_LOSS_50') and self.rt.settings.flags.stop_loss_card:
                expected.append('STOP_LOSS_CARD')
            if (reason!='BREAKEVEN_EXIT' and trade.get('success_detected_at') is not None
                    and self.rt.settings.flags.highest_card):expected.append('HIGHEST_CARD')
            suffixes=('', '_PRIVATE') if trade.get('publication_chat_id')=='both' else ('',)
            for suffix in suffixes:
                entry=await self.rt.db.fetchrow(q.EVENT_STATUS,'trade',trade['id'],'ENTRY_CARD'+suffix,0)
                if not entry or entry['status']!='SENT':continue
                for kind in expected:
                    event=await self.rt.db.fetchrow(q.EVENT_STATUS,'trade',trade['id'],kind+suffix,0)
                    if not event or event['status']!='SENT':return False
        return True

    async def tick(self):
        async with self.rt.db.pool.acquire() as conn:
            async with conn.transaction():
                for direction in ('CALL','PUT'):
                    await conn.execute(q.LOCK_TRADE_DIRECTION,direction)
                return await self._tick_locked()

    async def _tick_locked(self):
        rt=self.rt;day=et_date(utcnow())
        row=await rt.db.fetchrow(GET,day)
        if not row:return False
        if await rt.db.fetch(q.TRACKING_TRADES):return True
        trades=await rt.db.fetch(q.DAILY_TRADES.replace("  AND t.close_reason IS DISTINCT FROM 'OWNER_EXIT_EXCLUDED'", ""),day)
        revision=max((t["id"] for t in trades),default=0)
        if not await self.cards_sent(trades):return True
        groups=routing.report_groups(trades)
        target=routing.destination(row['publication_choice'])
        targets=(routing.PUBLIC_CHAT,routing.PRIVATE_CHAT) if target=='both' else (target,)
        ident=int(day.strftime('%Y%m%d'))
        for chat in targets:
            if et_date(utcnow())!=day:return True
            text,payload=daily_report_text(groups.get(chat,[]),day,rt.settings.tracking.success_premium_gain)
            text += "\n\nيشمل صفقات اليوم حتى الآن؛ يُحدّث بعد إغلاق أي عقد يدوي جديد."
            event='DAILY_REPORT_'+str(chat)
            await rt.publisher.text_once('daily',ident,event,text,payload=payload,chat_id=chat,level=revision)
            state=await rt.db.fetchrow(q.EVENT_STATUS,'daily',ident,event,revision)
            if state and state['status']=='SENT':
                await rt.publisher.text_once('daily',ident,'FINISH_GREETING_'+str(chat),CAPTION,chat_id=chat,level=revision)
        text,payload=daily_report_text(trades,day,rt.settings.tracking.success_premium_gain)
        await rt.publisher.text_once('daily',ident,'DAILY_REPORT',text,payload=payload,level=revision)
        return True

"""Daily owner consent for hourly public radar copies, never half-hour copies."""
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from . import channel_routing as routing, queries as q
from .timeutil import utcnow, et_date, within_session, to_et

GET = 'SELECT * FROM radar_sessions WHERE market_date=$1'
ENSURE = 'INSERT INTO radar_sessions(market_date) VALUES($1) ON CONFLICT DO NOTHING'
ENABLE = """UPDATE radar_sessions SET enabled=true,changed_at=now()
WHERE market_date=$1 AND NOT stopped RETURNING *"""
STOP = """INSERT INTO radar_sessions(market_date,stopped) VALUES($1,true)
ON CONFLICT(market_date) DO UPDATE SET stopped=true,enabled=false,changed_at=now()"""


def active(now, market):
    return to_et(now).weekday() < 5 and within_session(now, market.open_et, market.close_et)


async def offer(radar, day):
    if not routing.enabled():
        return
    await radar.db.execute(ENSURE, day)
    row = await radar.db.fetchrow(GET, day)
    if not row or row['enabled'] or row['stopped']:
        return
    ident = int(day.strftime('%Y%m%d'))
    await radar.publisher.text_once('radar_session', ident, 'CONSENT',
        '🦋 تشغيل رادار SPX كل ساعة في القناة العامة لهذه الجلسة؟\n'
        'عند الموافقة ينشر تقارير الساعة حتى نهاية الجلسة. لإيقافه لبقية اليوم: /RS',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton('نعم، لهذه الجلسة', callback_data=f'qrs:yes:{ident}'),
            InlineKeyboardButton('لا، اليوم', callback_data=f'qrs:no:{ident}')]]),
        max_attempts=1000000)


async def publish(radar, day, slot, text):
    if slot >= 100 or not routing.enabled():
        return
    now = utcnow()
    if et_date(now) != day or not active(now, radar.s.market):
        return
    row = await radar.db.fetchrow(GET, day)
    if not row or not row['enabled'] or row['stopped']:
        return
    ident = int(day.strftime('%Y%m%d'))
    # Same event as manual public copies: approval/restart cannot duplicate.
    await radar.publisher.text_once('radar', ident, 'RADAR_COPY_' + routing.PUBLIC_CHAT,
        text, level=slot, chat_id=routing.PUBLIC_CHAT,
        payload={'source':'LIQUIDITY_RADAR','session_consent':True}, max_attempts=1000000)


async def callback(rt, update, context):
    query = update.callback_query
    if (not query or not query.from_user or query.from_user.id != rt.settings.owner_user_id
            or not query.message or str(query.message.chat.id) != str(rt.settings.signal_chat_id)):
        if query:
            await query.answer('غير مصرح.', show_alert=True)
        return
    try:
        prefix, action, raw = query.data.split(':')
        day = datetime.strptime(raw, '%Y%m%d').date()
        if prefix != 'qrs' or action not in ('yes','no'):
            raise ValueError()
        if not rt.ready or day != et_date(utcnow()) or not active(utcnow(), rt.settings.market):
            await query.answer('هذا الطلب ليس لجلسة نشطة الآن.', show_alert=True)
            return
        event = await rt.db.fetchrow(q.EVENT_STATUS, 'radar_session', int(raw), 'CONSENT', 0)
        if not event or event['status'] != 'SENT' or event['message_id'] != query.message.message_id:
            await query.answer('استخدمي طلب اليوم الأصلي.', show_alert=True)
            return
        if action == 'yes':
            await query.answer('جارٍ تفعيل نشر الساعة…')
            await routing.check_permission(rt.app.bot, routing.PUBLIC_CHAT, rt.settings.signal_chat_id)
            row = await rt.db.fetchrow(ENABLE, day)
            text = ('✅ فُعّل نشر رادار الساعة بالعامة لهذه الجلسة. للإيقاف: /RS'
                    if row else 'نشر الرادار موقوف لبقية هذه الجلسة؛ يعود طلب الموافقة في الجلسة القادمة.')
        else:
            await rt.db.execute(STOP, day)
            await query.answer()
            text = 'لن يُنشر رادار الساعة تلقائيًا بالعامة لهذه الجلسة.'
        await query.edit_message_text(text)
    except ValueError:
        await query.answer('طلب غير صالح.', show_alert=True)


async def stop_command(rt, update, context):
    if (not update.effective_user or update.effective_user.id != rt.settings.owner_user_id
            or not update.effective_chat or str(update.effective_chat.id) not in
            (str(rt.settings.owner_user_id), str(rt.settings.signal_chat_id))):
        return
    if not await rt._guard(update):
        return
    await rt.db.execute(STOP, et_date(utcnow()))
    await update.message.reply_text('⏸ أُوقف نشر رادار الساعة بالعامة لبقية الجلسة. بقية البوت تعمل؛ يأتي طلب جديد في الجلسة القادمة.')

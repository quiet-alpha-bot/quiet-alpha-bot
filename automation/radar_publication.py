"""Owner-selected copies of persisted radar reports, deduplicated per channel."""
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from . import channel_routing as routing, queries as q
from .timeutil import utcnow, et_date

log = logging.getLogger('quiet-alpha.radar-publication')
SOURCES = {
    'company': ('company_radar', 'COMPANY_FLOW', 'SELECT * FROM company_radar_alerts WHERE id=$1'),
    'spx': ('spx_radar', 'SPX_UNUSUAL_FLOW', 'SELECT * FROM spx_radar_alerts WHERE id=$1'),
    'market': ('radar', 'LIQUIDITY_RADAR', None),
}


def keyboard(kind, ident, level=0):
    if not routing.enabled():
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f'qar:{kind}:{ident}:{level}:{choice}')
        for label, choice in [('🌐 العامة', 'public'), ('🔒 الخاصة', 'private'), ('📣 الاثنتان', 'both')]
    ]])


class RadarPublication:
    def __init__(self, runtime):
        self.rt = runtime

    async def callback(self, update, context):
        query = update.callback_query
        if query is None:
            return
        rt = self.rt
        if (query.from_user is None or rt.settings.owner_user_id is None
                or query.from_user.id != rt.settings.owner_user_id):
            await query.answer('⛔ غير مصرح', show_alert=True)
            return
        if (not rt.ready or not routing.enabled() or query.message is None
                or str(query.message.chat.id) != str(rt.settings.signal_chat_id)):
            await query.answer('استخدمي أزرار الرادار في الإدارة بعد اكتمال تشغيل البوت.', show_alert=True)
            return
        try:
            prefix, kind, ident, level, choice = query.data.split(':')
            ident, level = int(ident), int(level)
            if prefix != 'qar' or kind not in SOURCES or ident <= 0 or level < 0:
                raise ValueError('طلب غير صالح.')
            target = routing.destination(choice)
            entity, preview_event, sql = SOURCES[kind]
            if kind == 'market':
                day = datetime.strptime(str(ident), '%Y%m%d').date()
                row = await rt.db.fetchrow(q.GET_RADAR_REPORT, day, level)
            else:
                if level != 0:
                    raise ValueError('طلب غير صالح.')
                row = await rt.db.fetchrow(sql, ident)
            if row is None or row['market_date'] != et_date(utcnow()):
                raise ValueError('هذا التنبيه ليس من جلسة اليوم؛ اختاري تنبيهًا حديثًا.')
            preview = await rt.db.fetchrow(q.EVENT_STATUS, entity, ident, preview_event, level)
            if (not preview or preview['status'] != 'SENT'
                    or preview['message_id'] != query.message.message_id):
                raise ValueError('استخدمي رسالة الرادار الأصلية في الإدارة.')
            await query.answer('جارٍ نشر الرادار…')
            targets = (routing.PUBLIC_CHAT, routing.PRIVATE_CHAT) if target == routing.BOTH_CHATS else (target,)
            # Check all permissions before beginning a both-channel delivery.
            await routing.check_permission(rt.app.bot, target, rt.settings.signal_chat_id)
            states = []
            for chat in targets:
                event = 'RADAR_COPY_' + str(chat)
                await rt.publisher.text_once(entity, ident, event, row['message_text'], level=level,
                    chat_id=chat, payload={'source': preview_event}, max_attempts=3)
                state = await rt.db.fetchrow(q.EVENT_STATUS, entity, ident, event, level)
                states.append(state['status'] if state else 'PENDING')
            if all(state == 'SENT' for state in states):
                result = '✅ الرادار منشور في ' + routing.channel_name(target) + '.'
            elif 'UNCERTAIN' in states:
                result = '⚠️ تعذر تأكيد وصول إحدى النسخ؛ راجعي القناة قبل أي إعادة إرسال.'
            else:
                result = 'لم يكتمل نشر الرادار في كل الوجهات. يمكنك إعادة الضغط؛ النسخة المؤكدة لن تتكرر.'
            await query.message.reply_text(result)
        except ValueError as error:
            await query.message.reply_text(str(error))
        except Exception:
            log.exception('Radar publication failed')
            await query.message.reply_text('تعذر إكمال نشر الرادار. راجعي القنوات قبل المحاولة مجددًا.')

"""Owner-selected destinations. Execution channel is simulation-only."""
import os
from decimal import Decimal
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from . import queries as q, channel_routing as routing
from .hero import is_hero
from .models import ProposalStatus

EXECUTION_CHAT = '-1003975189482'

def enabled():
    return routing.enabled() and os.getenv('CHANNEL_SELECTION_ENABLED','false').lower() in ('true','1','yes')

def route(mask):
    if type(mask) is not int or mask < 1 or mask > 7:
        raise ValueError('حددي قناة واحدة على الأقل.')
    return {0:EXECUTION_CHAT,1:routing.PUBLIC_CHAT,2:routing.PRIVATE_CHAT,3:'both'}[mask & 3]

def keyboard(pid, mask=0, price=None):
    kind = 'a' if price is None else str(price)
    def data(action):return f'qs:{action}:{pid}:{kind}'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(('✅ ' if mask & bit else '⬜ ')+label,
                              callback_data=data(str(bit)))
         for bit,label in ((1,'العامة'),(2,'الخاصة'),(4,'البوت 🧪'))],
        [InlineKeyboardButton('📤 إرسال',callback_data=data('send')),
         InlineKeyboardButton('❌ إلغاء',callback_data=data('cancel'))]])

class Proxy:
    def __init__(self, original, **values):self.original,self.values=original,values
    def __getattr__(self, key):return self.values[key] if key in self.values else getattr(self.original,key)

async def callback(rt, update, context):
    query=update.callback_query
    if query is None:return
    if (query.from_user is None or query.from_user.id != rt.settings.owner_user_id
            or query.message is None):
        await query.answer('غير مصرح.',show_alert=True);return
    if not enabled() or not rt.ready:
        await query.answer('اختيار القنوات غير جاهز.',show_alert=True);return
    try:
        _,action,pid,kind=query.data.split(':');pid=int(pid)
        manual=kind!='a'
        if ((manual and query.message.chat.type!='private') or
            (not manual and str(query.message.chat.id)!=str(rt.settings.signal_chat_id))):
            raise ValueError('استخدمي معاينة الإدارة الأصلية.')
        row=await rt.db.fetchrow(q.GET_PROPOSAL,pid)
        if row is None:raise ValueError('المقترح غير موجود.')
        if (row['classification']=='MANUAL') != manual:raise ValueError('نوع معاينة غير مطابق.')
        if action=='cancel':
            data=f'qam:cancel:{pid}' if manual else f'qa:rj:{pid}'
            forwarded=Proxy(update,callback_query=Proxy(query,data=data))
            await (rt.manual.callback(forwarded,context) if manual else rt.approval.on_callback(forwarded,context))
            return
        if action in ('1','2','4'):
            if action=='4' and is_hero(row):raise ValueError('HERO OR ZERO غير متاح للمحاكاة.')
            row=await rt.db.fetchrow("""UPDATE proposals SET channel_selection_mask=channel_selection_mask # $2
                WHERE id=$1 AND status='PENDING_APPROVAL' AND NOT channel_selection_locked RETURNING *""",pid,int(action))
            if row is None:raise ValueError('تم تثبيت الاختيار أو معالجة الطلب.')
            await query.edit_message_reply_markup(reply_markup=keyboard(pid,row['channel_selection_mask'],Decimal(kind) if manual else None))
            await query.answer('تم تحديث التحديد؛ اضغطي إرسال للاعتماد.');return
        if action!='send':raise ValueError('طلب غير صالح.')
        mask=row['channel_selection_mask'];chosen=route(mask)
        if mask & 4 and is_hero(row):raise ValueError('HERO OR ZERO غير متاح للمحاكاة.')
        await routing.check_permission(rt.app.bot,chosen,rt.settings.signal_chat_id)
        if mask & 4:await routing.check_permission(rt.app.bot,EXECUTION_CHAT,rt.settings.signal_chat_id)
        frozen=await rt.db.fetchrow("""UPDATE proposals SET channel_selection_locked=true
            WHERE id=$1 AND status='PENDING_APPROVAL' AND channel_selection_mask=$2
            AND NOT channel_selection_locked RETURNING *""",pid,mask)
        if frozen is None:raise ValueError('تمت معالجة الطلب أو تغير الاختيار؛ أعيدي المحاولة.')
        try:
            if manual:
                action={routing.PUBLIC_CHAT:'public',routing.PRIVATE_CHAT:'private','both':'both',EXECUTION_CHAT:'execution'}[chosen]
                forwarded=Proxy(update,callback_query=Proxy(query,data=f'qam:{action}:{pid}:{kind}',selection_dispatch=True))
                await rt.manual.callback(forwarded,context)
            else:
                await rt.approval._approve(query,pid,ProposalStatus.PENDING_APPROVAL,query.from_user.id,chosen)
        finally:
            # Retry remains possible after price/market/permission validation declines.
            # APPROVING, NEEDS_RECONFIRM and APPROVED retain the frozen selection.
            await rt.db.execute("""UPDATE proposals SET channel_selection_locked=false
                WHERE id=$1 AND status='PENDING_APPROVAL' AND channel_selection_mask=$2""",pid,mask)
    except (ValueError,TypeError,KeyError):
        await query.answer('تعذر الاعتماد؛ تحققي من التحديد وصلاحيات القنوات وحالة المقترح.',show_alert=True)

PENDING = """SELECT t.* FROM trades t JOIN proposals p ON p.id=t.proposal_id
WHERE p.channel_selection_locked AND (p.channel_selection_mask & 4)=4
AND p.status='APPROVED' AND t.status='TRACKING' AND t.publication_chat_id<>$1
AND COALESCE(t.approval_quote->>'trade_mode','')<>'HERO_OR_ZERO'
AND EXISTS (SELECT 1 FROM events e WHERE e.entity_type='trade' AND e.entity_id=t.id
 AND e.event_type IN ('ENTRY_CARD','ENTRY_CARD_PRIVATE') AND e.status='SENT')
AND EXISTS (SELECT 1 FROM events e WHERE e.entity_type='trade' AND e.entity_id=t.id
 AND e.event_type IN ('ENTRY_CARD','ENTRY_CARD_PRIVATE') AND e.status='SENT'
 AND (e.payload->>'telegram_sent_at')::timestamptz >= now()-interval '60 seconds')
AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity_type='simulation_trade' AND e.entity_id=t.id
 AND e.event_type='SIMULATION_ENTRY' AND e.status IN ('SENT','UNCERTAIN','PENDING','NEEDS_RECONCILIATION'))
ORDER BY t.id LIMIT 50"""

async def publish_pending(rt):
    if not enabled():return
    for trade in await rt.db.fetch(PENDING,EXECUTION_CHAT):
        if is_hero(trade):continue
        await rt.publisher.text_once('simulation_trade',trade['id'],'SIMULATION_ENTRY',
            f"🧪 محاكاة فقط — ليست صفقة منفذة\nمعرف الصفقة: {trade['id']}\n"
            f"{trade['option_symbol']}\nسعر القناة المرجعي: {trade['approved_entry']}\n"
            'بانتظار قراءة سعر الوسيط وفحص شروط المحاكاة.',chat_id=EXECUTION_CHAT,
            payload={'trade_id':trade['id'],'option_symbol':trade['option_symbol'],
                     'mode':'SIMULATION_ONLY'})

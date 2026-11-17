"""Explicit owner reconciliation for a missing prayer preview; never auto-resend."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .opening_prayer import GET
from .timeutil import et_date,utcnow

RESET = """UPDATE events SET status='FAILED',attempts=0,last_error='OWNER_CONFIRMED_PREVIEW_ABSENT',updated_at=now()
WHERE entity_type='opening_prayer' AND entity_id=$1 AND event_type='PREVIEW' AND level=0
AND status IN ('UNCERTAIN','NEEDS_RECONCILIATION','FAILED') RETURNING id"""

async def command(rt,update,context):
    if not await rt._guard(update):return
    if str(update.effective_chat.id) not in (str(rt.settings.owner_user_id),str(rt.settings.signal_chat_id)):return
    try:
        if len(context.args)!=1:raise ValueError
        ident=int(context.args[0].lstrip('#'))
        if ident<=0:raise ValueError
    except (ValueError,TypeError):
        await update.message.reply_text('الاستخدام: /qa_prayer_retry رقم_الدعاء');return
    await update.message.reply_text(f'راجعي الإدارة أولًا: هل معاينة الدعاء #{ident} لم تصل؟ إعادة الإرسال بعد وصولها قد تكررها.',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('لم تصل — أعيدي المعاينة',callback_data=f'qpr:{ident}:retry'),
            InlineKeyboardButton('إلغاء',callback_data=f'qpr:{ident}:cancel')]]))

async def callback(rt,update,context):
    from .delete_trade import allowed
    query=update.callback_query
    if not allowed(rt,update):
        if query:await query.answer('غير مصرح.',show_alert=True)
        return
    if not rt.ready:
        await query.answer('البوت غير جاهز.',show_alert=True);return
    try:
        _,raw,action=query.data.split(':');ident=int(raw)
        if action=='cancel':
            await query.answer('أُلغي الطلب.');await query.edit_message_reply_markup(reply_markup=None);return
        if action!='retry':raise ValueError
        row=await rt.db.fetchrow(GET,ident)
        if not row or row['status']!='PENDING' or row['market_date']!=et_date(utcnow()):
            await query.answer('ليست معاينة اليوم المعلّقة؛ لم تُرسل نسخة جديدة.',show_alert=True);return
        if not await rt.db.fetchrow(RESET,ident):
            await query.answer('لا توجد معاينة متعثرة قابلة للإعادة؛ لم نكررها.',show_alert=True);return
        await query.answer('سُجل تأكيدك؛ تُعاد المعاينة فقط.')
        await query.edit_message_reply_markup(reply_markup=None)
        await rt.opening_prayer.photo(row,'PREVIEW',rt.settings.signal_chat_id,preview=True)
    except (ValueError,TypeError):
        await query.answer('طلب غير صالح.',show_alert=True)

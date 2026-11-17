"""Silent, durable trade cancellation, serialized with trade deliveries."""
from contextlib import asynccontextmanager
from . import queries as q

REASON = 'OWNER_DELETED'
LOCK = 'SELECT pg_advisory_lock($1::bigint)'
UNLOCK = 'SELECT pg_advisory_unlock($1::bigint)'
DELETE = """UPDATE trades SET status='CLOSED', close_reason='OWNER_DELETED',
closed_at=COALESCE(closed_at,now()) WHERE id=$1 RETURNING *"""

@asynccontextmanager
async def delivery_guard(db, trade_id):
    # Negative keys keep these locks separate from entry/direction locks.
    if getattr(db, 'pool', None) is None:  # In-memory database adapters used by the unit tests.
        yield db
        return
    async with db.pool.acquire() as conn:
        key = -7000000000000000000 + trade_id
        await conn.execute(LOCK, key)
        try:
            yield conn
        finally:
            await conn.execute(UNLOCK, key)

def allowed(runtime, update):
    query=update.callback_query
    return (query and query.from_user and query.from_user.id==runtime.settings.owner_user_id
            and query.message and str(query.message.chat.id) in
            (str(runtime.settings.owner_user_id),str(runtime.settings.signal_chat_id)))

async def command(runtime, update, context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    if not await runtime._guard(update):return
    if str(update.effective_chat.id) not in (str(runtime.settings.owner_user_id),str(runtime.settings.signal_chat_id)):return
    try:
        if len(context.args)!=1:raise ValueError
        ident=int(context.args[0].lstrip('#'))
        if not 0<ident<10**15:raise ValueError
    except (ValueError,TypeError):
        await update.message.reply_text('الاستخدام: /D رقم_الصفقة، مثل /D 318');return
    trade=await runtime.db.fetchrow(q.GET_TRADE,ident)
    if not trade:
        await update.message.reply_text(f'لم أجد الصفقة #{ident}.');return
    markup=InlineKeyboardMarkup([[InlineKeyboardButton(label,callback_data=f'qdel:{ident}:{choice}:choose')
        for label,choice in [('🌐 العامة','public'),('🔒 الخاصة','private'),('📣 الاثنتان','both'),('إلغاء','cancel')]]])
    await update.message.reply_text(f'من أي قناة تُلغى متابعة الصفقة #{ident} وتُستبعد من البيان؟',reply_markup=markup)

APPLY = """UPDATE trades SET deleted_channels=$2::jsonb,
status=CASE WHEN $3 THEN 'CLOSED' ELSE status END,
close_reason=CASE WHEN $3 THEN $4 ELSE close_reason END,
closed_at=CASE WHEN $3 THEN COALESCE(closed_at,now()) ELSE closed_at END
WHERE id=$1 RETURNING *"""

async def callback(runtime,update,context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from . import channel_routing as routing
    from .channel_scope import targets
    import json
    query=update.callback_query
    if not allowed(runtime,update):
        if query:await query.answer('غير مصرح.',show_alert=True)
        return
    if not runtime.ready:
        await query.answer('البوت غير جاهز.',show_alert=True);return
    try:
        prefix,raw,choice,stage=query.data.split(':');ident=int(raw)
        if prefix!='qdel' or not 0<ident<10**15 or stage not in ('choose','confirm'):raise ValueError
        if choice=='cancel':
            await query.answer('أُلغي الطلب.');await query.edit_message_reply_markup(reply_markup=None);return
        route=routing.destination(choice)
        if stage=='choose':
            await query.answer()
            await query.edit_message_text(f'تأكيد إلغاء الصفقة #{ident} من {routing.channel_name(route)}؟ ستتوقف تحديثاتها وتُستبعد من بيان هذه الوجهة فقط.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('تأكيد',callback_data=f'qdel:{ident}:{choice}:confirm'),
                InlineKeyboardButton('إلغاء',callback_data=f'qdel:{ident}:cancel:choose')]]))
            return
        async with delivery_guard(runtime.db,ident) as conn:
            trade=await conn.fetchrow(q.GET_TRADE,ident)
            if not trade:raise ValueError
            old=trade.get('deleted_channels') or []
            if isinstance(old,str):old=json.loads(old)
            actual=set(targets(trade.get('publication_chat_id')))
            chosen=set(targets(route)) & actual
            if not chosen:
                await query.answer('الصفقة غير منشورة في هذه القناة.',show_alert=True);return
            removed=set(old)|chosen
            all_removed=actual<=removed
            # If public is removed after the private copy has closed, no active copy remains.
            no_live_copy=all_removed or (trade.get('private_close') and routing.PUBLIC_CHAT in removed)
            await conn.fetchrow(APPLY,ident,list(sorted(removed)),bool(no_live_copy),'OWNER_DELETED' if all_removed else 'CHANNELS_COMPLETE')
        await query.answer('تم التنفيذ.')
        await query.edit_message_text(f'✅ تم إلغاء متابعة الصفقة #{ident} واستبعادها من بيان {routing.channel_name(route)}. لم تُرسل رسالة للقنوات.')
    except (ValueError,TypeError):
        await query.answer('طلب غير صالح؛ أرسلي /D من جديد.',show_alert=True)

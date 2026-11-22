"""Owner-only requests for the separate local simulation. Never closes source trades."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

async def command(runtime, update, context):
    if not await runtime._guard(update):
        return
    chat = update.effective_chat.id
    owner = runtime.settings.owner_user_id
    if str(chat) not in (str(owner),str(runtime.settings.signal_chat_id)):
        return
    if context.args:
        await update.message.reply_text('اكتبي /X لعرض صفقات التنفيذ التجريبي المفتوحة واختيار الصفقة.')
        return
    await runtime.db.execute("INSERT INTO execution_requests(kind,owner_id,chat_id) VALUES('LIST',$1,$2)",owner,chat)
    await update.message.reply_text('طلبت قائمة صفقات التنفيذ المفتوحة. يجب أن تكون المحاكاة المحلية وربطها شغّالين؛ الطلب صالح لدقيقتين.')

async def callback(runtime, update, context):
    query=update.callback_query
    owner=runtime.settings.owner_user_id
    if (str(query.from_user.id)!=str(owner) or
        str(query.message.chat.id) not in (str(owner),str(runtime.settings.signal_chat_id))):
        await query.answer('غير مصرح.',show_alert=True)
        return
    parts=query.data.split(':')
    action=parts[1] if len(parts)>1 else ''
    try:
        ident=int(parts[2])
        if not 0<ident<2**63:raise ValueError()
        if action=='pick' and len(parts)==4:
            trade=int(parts[3])
            if not 0<trade<2**63:raise ValueError()
            row=await runtime.db.fetchrow("""INSERT INTO execution_requests(kind,owner_id,chat_id,trade_id)
                SELECT 'CONFIRM',owner_id,chat_id,$4 FROM execution_requests
                WHERE id=$1 AND owner_id=$2 AND chat_id=$3 AND kind='LIST' AND expires_at>now()
                RETURNING id""",ident,owner,query.message.chat.id,trade)
            if not row:
                await query.answer('انتهت صلاحية القائمة؛ أرسلي /X مجددًا.',show_alert=True);return
            await query.answer()
            await query.message.reply_text(f'تأكيد إغلاق صفقة التنفيذ التجريبي #{trade} بأول Bid حديث متاح؟\nلا يوقف استقبال الصفقات ولا يغلق صفقة الخاصة.',reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton('تأكيد الإغلاق',callback_data=f'qex:confirm:{row["id"]}'),
                InlineKeyboardButton('إلغاء',callback_data=f'qex:cancel:{row["id"]}')]]))
        elif action in ('confirm','cancel') and len(parts)==3:
            row=await runtime.db.fetchrow("""UPDATE execution_requests SET kind=$4,
                expires_at=now()+interval '60 seconds'
                WHERE id=$1 AND owner_id=$2 AND chat_id=$3 AND kind='CONFIRM' AND expires_at>now()
                RETURNING trade_id""",ident,owner,query.message.chat.id,'EXIT' if action=='confirm' else 'CANCEL')
            if not row:
                await query.answer('الطلب منتهٍ أو سبق التعامل معه.',show_alert=True);return
            await query.answer()
            await query.edit_message_text('أُرسل طلب الإغلاق للمحاكاة؛ انتظري تأكيد النتيجة.' if action=='confirm' else 'أُلغي الطلب.')
        else:raise ValueError()
    except (ValueError,IndexError):
        await query.answer('طلب غير صالح.',show_alert=True)

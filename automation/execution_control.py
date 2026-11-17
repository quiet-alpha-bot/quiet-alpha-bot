"""Owner-only intake controls; open simulated positions are never paused."""
GET = 'SELECT * FROM execution_control WHERE id=1'
SET = """UPDATE execution_control SET paused=$1,
resumed_at=CASE WHEN NOT $1 AND paused THEN now() ELSE resumed_at END,
changed_at=now(),revision=revision+1 WHERE id=1 RETURNING *"""

async def command(runtime, update, context, paused):
    if not await runtime._guard(update):
        return
    if str(update.effective_chat.id) not in (str(runtime.settings.owner_user_id),str(runtime.settings.signal_chat_id)):
        return
    if context.args:
        await update.message.reply_text('الاستخدام: /P للإيقاف أو /R للتشغيل، دون أرقام.')
        return
    await runtime.db.fetchrow(SET, paused)
    await update.message.reply_text(
        '⏸ توقف استقبال صفقات جديدة في بوت التنفيذ التجريبي. تستمر إدارة العقود المفتوحة؛ نشر الخاصة مستمر.'
        if paused else '▶️ فُعّل استقبال الصفقات الجديدة من الخاصة في بوت التنفيذ التجريبي. لن تؤخذ صفقات فترة الإيقاف.')

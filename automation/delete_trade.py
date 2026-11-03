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

async def command(runtime, update, context):
    if not await runtime._guard(update):return
    if str(update.effective_chat.id) not in (str(runtime.settings.owner_user_id),
                                            str(runtime.settings.signal_chat_id)):
        return
    try:
        if len(context.args)!=1:raise ValueError
        ident=int(context.args[0].lstrip('#'))
        if ident<=0 or ident>=10**15:raise ValueError
    except (ValueError,TypeError):
        await runtime.publisher.text('الاستخدام: /D رقم_الصفقة، مثل /D 318')
        return
    async with delivery_guard(runtime.db,ident) as conn:
        trade=await conn.fetchrow(DELETE,ident)
    text=(f'✅ تم تنفيذ الأمر للصفقة #{ident}: أُلغيت متابعتها في القناتين واستُبعدت من البيان.'
          if trade else f'لم أجد الصفقة #{ident}.')
    # No message is sent to either publication channel, including on repeats.
    await runtime.publisher.text(text)

"""Channel stop policy and immutable private closure views."""
from datetime import datetime
from decimal import Decimal
from .channel_routing import PUBLIC_CHAT, PRIVATE_CHAT, BOTH_CHATS

ARM = """UPDATE trades SET recovered_at=$2 WHERE id=$1 AND status='TRACKING'
AND recovered_at IS NULL AND success_detected_at IS NULL RETURNING *"""
FREEZE_PRIVATE = """UPDATE trades SET private_close=$2 WHERE id=$1 AND status='TRACKING'
AND publication_chat_id='both' AND private_close IS NULL RETURNING *"""
PENDING_PRIVATE = """SELECT t.* FROM trades t WHERE private_close IS NOT NULL
AND close_reason IS DISTINCT FROM 'OWNER_DELETED'
AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity_type='trade' AND e.entity_id=t.id
AND e.event_type='STOP_LOSS_CARD_PRIVATE' AND e.status IN ('SENT','UNCERTAIN','NEEDS_RECONCILIATION'))"""
PENDING_RECOVERY = """SELECT * FROM trades WHERE close_reason='RECOVERY_STOP'
AND status='CLOSED' ORDER BY closed_at DESC LIMIT 100"""
PENDING_RETURN = """SELECT * FROM trades WHERE recovered_at IS NOT NULL
AND status='TRACKING' ORDER BY id"""

def stop_pct(trade, fallback=Decimal('.50')):
    route=trade.get('publication_chat_id')
    from .channel_scope import deleted
    if route==BOTH_CHATS and deleted(trade,PUBLIC_CHAT):return Decimal('.35')
    if route==PRIVATE_CHAT:return Decimal('.35')
    if route in (PUBLIC_CHAT,BOTH_CHATS):return Decimal('.50')
    return fallback

def private_closed(trade):
    return bool(trade and trade.get('private_close'))

def snapshot(trade, now):
    keys=('current_bid','current_ask','current_last','highest_bid','lowest_bid',
          'highest_bid_time','lowest_bid_time','highest_mid','highest_last')
    data={k:trade.get(k) for k in keys}
    data.update(status='CLOSED',close_reason='STOP_LOSS_35',closed_at=now,
                success_detected_at=None,success_sent=False,success_bid=None)
    return data

def channel_view(trade, target):
    copy=dict(trade)
    if target==PRIVATE_CHAT and private_closed(trade):
        for key,value in trade['private_close'].items():
            if value is not None and key in ('closed_at','highest_bid_time','lowest_bid_time'):
                value=datetime.fromisoformat(value) if isinstance(value,str) else value
            if value is not None and key in ('current_bid','current_ask','current_last','highest_bid',
                    'lowest_bid','highest_mid','highest_last','success_bid'):
                value=Decimal(str(value))
            copy[key]=value
    return copy

def profit_level(trade):
    return Decimal(trade['approved_entry']) + Decimal('.10')


def profit_armed(trade):
    return bool((trade.get('approval_quote') or {}).get('recovery_profit_at'))


def profit_exit(trade, bid):
    meta = trade.get('approval_quote') or {}
    return (profit_armed(trade) and bool(meta.get('recovery_profit_above'))
            and not meta.get('recovery_continue') and bid <= profit_level(trade))


CONTINUE = """UPDATE trades SET approval_quote=COALESCE(approval_quote,'{}'::jsonb)
||jsonb_build_object('recovery_continue',true), updated_at=now()
WHERE id=$1 AND status='TRACKING'
AND coalesce(approval_quote->>'trade_mode','NORMAL') != 'HERO_OR_ZERO'
RETURNING id"""


async def continue_command(runtime, update, context):
    # Keep both the command and acknowledgement out of every channel.
    owner = runtime.settings.owner_user_id
    if (not update.effective_user or update.effective_user.id != owner
            or not update.effective_chat or str(update.effective_chat.id) != str(owner)):
        return
    if not await runtime._guard(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit() or not 0 < int(context.args[0]) < 2**63:
        await update.message.reply_text('اكتبي /C ثم رقم الصفقة، مثل /C 636.')
        return
    row = await runtime.db.fetchrow(CONTINUE, int(context.args[0]))
    await update.message.reply_text(
        f"✅ تم تطبيق الأمر على الصفقة #{row['id']}." if row
        else 'لا توجد صفقة مفتوحة مناسبة بهذا الرقم؛ الأمر لا يعيد فتح صفقة مغلقة.')


def recovered_text(trade):
    meta = trade.get('approval_quote') or {}
    bid = Decimal(str(meta.get('recovery_profit_bid', trade['current_bid'])))
    return ('🦋 <b>QUIET ALPHA — تعافي العقد</b>\n\n'
            f"<b>{trade['option_symbol']}</b>\n\n"
            f"سعر الدخول: <b>${Decimal(trade['approved_entry']):.2f}</b>\n"
            f"السعر المرصود: <b>${bid:.2f}</b>\n\n"
            'عاد العقد فوق سعر الدخول؛ تستمر متابعة حركته ورصد مستجداته.\n\n'
            '🔔 <b>تابعوا حركة العقد عن قرب، وانتبهوا لأي تراجع بعد التعافي.</b>')


def close_text(trade):
    entry=Decimal(trade['approved_entry']);bid=Decimal(trade['current_bid'])
    return ('🦋 <b>QUIET ALPHA — إغلاق متابعة العقد</b>\n\n'
        f"<b>{trade['option_symbol']}</b>\n\n"
        f"سعر الدخول: <b>${entry:.2f}</b>\n"
        f"سعر الإغلاق المرصود: <b>${bid:.2f}</b>\n"
        f"نتيجة العقد المرصودة قبل الرسوم: <b>{(bid-entry)*100:+.2f}$</b>\n\n"
        'أُغلقت متابعة العقد بعد تراجع السعر من أعلى مستوى سجّله عقب التعافي.')

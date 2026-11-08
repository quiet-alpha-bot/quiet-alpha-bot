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

def recovered_text(trade):
    return ('🦋 <b>QUIET ALPHA — تعافي العقد</b>\n\n'
            f"<b>{trade['option_symbol']}</b>\n"
            'عاد العقد إلى سعر الدخول؛ تستمر متابعة حركته ورصد مستجداته.')

def close_text(trade):
    entry=Decimal(trade['approved_entry']);bid=Decimal(trade['current_bid'])
    return ('🦋 <b>إغلاق متابعة العقد لحماية رأس المال</b>\n\n'
        f"<b>{trade['option_symbol']}</b>\n"
        f"سعر الدخول: ${entry:.2f}\nأعلى سعر مرصود: ${Decimal(trade['highest_bid']):.2f}\n"
        f"أقل سعر مرصود: ${Decimal(trade['lowest_bid']):.2f}\n"
        f"سعر الإغلاق المرصود: ${bid:.2f}\nنتيجة العقد المرصودة: {(bid-entry)*100:+.2f}$\n\n"
        'تم إغلاق متابعة العقد للحد من الخسارة وحماية رأس المال.')

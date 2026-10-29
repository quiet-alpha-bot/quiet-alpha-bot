"""Explicit owner-selected publishing channels; operational output stays admin."""
import os
from . import queries as q

PUBLIC_CHAT = '-1003813791281'
PRIVATE_CHAT = '-1004417588627'
BOTH_CHATS = 'both'
PUBLIC_EVENTS = frozenset({'ENTRY_CARD','MILESTONE','SUCCESS_CARD','HIGHEST_CARD',
                          'STOP_LOSS_CARD','BREAKEVEN_APPROACH','BREAKEVEN_EXIT'})
# TRADE_EXIT is administrative: protection still closes tracking, while the
# publication channel receives its result card without the generic exit text.


def enabled():
    # Last deployment step: opt in only after every file and migration is uploaded.
    return os.getenv('CHANNEL_ROUTING_ENABLED','false').lower() in ('true','1','yes')


def destination(choice):
    if choice not in ('public','private','both'):
        raise ValueError('اختاري العامة أو الخاصة أولًا.')
    return BOTH_CHATS if choice == 'both' else PUBLIC_CHAT if choice == 'public' else PRIVATE_CHAT


def channel_name(chat_id):
    return 'العامة والخاصة' if chat_id == BOTH_CHATS else 'العامة' if str(chat_id) == PUBLIC_CHAT else 'الخاصة' if str(chat_id) == PRIVATE_CHAT else 'الإدارة'


async def check_permission(bot, chat_id, admin_chat):
    if chat_id == BOTH_CHATS:
        for target in (PUBLIC_CHAT,PRIVATE_CHAT):
            await check_permission(bot,target,admin_chat)
        return
    if str(chat_id) == str(admin_chat):
        raise ValueError('قناة النشر يجب أن تختلف عن قناة الإدارة.')
    member = await bot.get_chat_member(chat_id=chat_id,user_id=bot.id)
    if member.status not in ('creator','administrator') or (member.status != 'creator' and not member.can_post_messages):
        raise ValueError('البوت يحتاج صلاحية نشر الرسائل في القناة المختارة.')


def event_for_channel(event, target, route):
    return event + '_PRIVATE' if route == BOTH_CHATS and target == PRIVATE_CHAT else event


async def trade_destination(db, entity_type, entity_id, event_type, admin_chat):
    if entity_type != 'trade' or (event_type not in PUBLIC_EVENTS and event_type != 'TRADE_EXIT'):
        return admin_chat
    trade = await db.fetchrow(q.GET_TRADE,entity_id)
    if event_type == 'TRADE_EXIT' and (not trade or trade.get('close_reason') not in
            {'OWNER_EXIT','OWNER_EXIT_EXCLUDED','OWNER_EXIT_FLAT'}):
        return admin_chat
    # Existing trades have NULL and keep their original admin destination.
    return (trade.get('publication_chat_id') if trade else None) or admin_chat


BEGIN_ROUTED_APPROVAL = """
UPDATE proposals SET status='APPROVING', approving_from=status,
    approving_since=now(), updated_at=now(), publication_chat_id=$3
WHERE id=$1 AND status=$2 AND classification IS DISTINCT FROM 'MANUAL'
    AND (expires_at IS NULL OR expires_at > now())
RETURNING *
"""
SET_MANUAL_CHANNEL = """UPDATE proposals SET publication_chat_id=$2
WHERE id=$1 AND status='PENDING_APPROVAL' AND classification='MANUAL'"""
PENDING_CLOSE_PUBLICATIONS = """
SELECT t.* FROM trades t
WHERE t.status='CLOSED' AND t.publication_chat_id IS NOT NULL
AND EXISTS (SELECT 1 FROM events e WHERE e.entity_type='trade' AND e.entity_id=t.id
            AND e.event_type IN ('ENTRY_CARD','ENTRY_CARD_PRIVATE') AND e.status='SENT')
AND EXISTS (
    SELECT 1 FROM (VALUES ('TRADE_EXIT'),('STOP_LOSS_CARD'),('HIGHEST_CARD'),('BREAKEVEN_EXIT')) AS needed(kind)
    CROSS JOIN (VALUES (''),('_PRIVATE')) AS copy(suffix)
    WHERE (copy.suffix='' OR t.publication_chat_id='both')
    AND (copy.suffix='' OR kind<>'TRADE_EXIT' OR t.close_reason IN ('OWNER_EXIT','OWNER_EXIT_EXCLUDED','OWNER_EXIT_FLAT'))
    AND EXISTS (SELECT 1 FROM events entry WHERE entry.entity_type='trade' AND entry.entity_id=t.id
        AND entry.event_type='ENTRY_CARD'||copy.suffix AND entry.status='SENT')
    AND ((kind='STOP_LOSS_CARD' AND t.close_reason IN ('STOP_LOSS_30','STOP_LOSS_50'))
        OR (kind='BREAKEVEN_EXIT' AND t.close_reason='BREAKEVEN_EXIT')
        OR (kind='HIGHEST_CARD' AND t.success_detected_at IS NOT NULL AND t.close_reason<>'BREAKEVEN_EXIT')
        OR (kind='TRADE_EXIT' AND t.close_reason NOT IN ('BREAKEVEN_EXIT','STOP_LOSS_30','STOP_LOSS_50')))
    AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity_type='trade' AND e.entity_id=t.id
                    AND e.event_type=kind||copy.suffix AND (e.status<>'FAILED' OR e.attempts>=3))
) ORDER BY t.closed_at LIMIT 50
"""


def report_groups(trades):
    """Each actually published copy belongs to its channel; admin retains one trade."""
    groups = {}
    for trade in trades:
        chat = trade.get('publication_chat_id')
        if chat == BOTH_CHATS:
            for target,mid,timestamp in [(PUBLIC_CHAT,'entry_message_id','published_at'),
                                         (PRIVATE_CHAT,'private_entry_message_id','private_published_at')]:
                if trade.get(mid) is not None:
                    copy=dict(trade,publication_chat_id=target,entry_message_id=trade[mid],
                              published_at=trade.get(timestamp))
                    groups.setdefault(target,[]).append(copy)
        elif chat and trade.get('entry_message_id') is not None:
            groups.setdefault(chat,[]).append(trade)
    return groups

"""Channel membership for capacity, deletion and delivery (route stays immutable)."""
import json
from .channel_routing import PUBLIC_CHAT, PRIVATE_CHAT

def deleted(trade, chat):
    value = (trade or {}).get('deleted_channels') or []
    if isinstance(value, str):
        value = json.loads(value)
    return str(chat) in value

def targets(route):
    return (PUBLIC_CHAT, PRIVATE_CHAT) if route == 'both' else (route,) if route else (None,)

COUNT = """SELECT count(*) FROM trades WHERE status='TRACKING' AND direction=$1
AND (publication_chat_id=$2 OR publication_chat_id='both' OR ($2::text IS NULL AND publication_chat_id IS NULL))
AND NOT (deleted_channels ? COALESCE($2::text,''))
AND ($2::text <> '-1004417588627' OR $2 IS NULL OR private_close IS NULL)"""
STRIKE = """SELECT EXISTS(SELECT 1 FROM trades WHERE status='TRACKING' AND direction=$1 AND strike=$2
AND (publication_chat_id=$3 OR publication_chat_id='both' OR ($3::text IS NULL AND publication_chat_id IS NULL))
AND NOT (deleted_channels ? COALESCE($3::text,''))
AND ($3::text <> '-1004417588627' OR $3 IS NULL OR private_close IS NULL))"""

async def capacity(conn, direction, strike, route):
    counts=[]; busy=False
    for chat in targets(route):
        counts.append(await conn.fetchval(COUNT,direction,chat))
        busy = bool(await conn.fetchval(STRIKE,direction,strike,chat)) or busy
    return max(counts,default=0), busy

"""Channel-scoped reports, with complete, HTML-safe Telegram pagination."""
from datetime import timedelta
from . import channel_routing as routing
from .daily_report import daily_report_text


def pages(text, limit=3500):
    # All report HTML tags are balanced on individual lines. Never split a tag.
    result=[]; current=[]; size=0
    for line in text.splitlines():
        if size+len(line)+1>limit and current:
            result.append('\n'.join(current)); current=[]; size=0
        current.append(line); size+=len(line)+1
    if current:result.append('\n'.join(current))
    return result


def admin_report(trades, day, gain, preview=False):
    groups=routing.report_groups(trades)
    parts=['📊 <b>QUIET ALPHA — بيان الإدارة اليومي</b>'+(' (معاينة)' if preview else '')]
    for chat,title in ((routing.PUBLIC_CHAT,'🌐 العامة'),(routing.PRIVATE_CHAT,'🔒 الخاصة')):
        text,_=daily_report_text(groups.get(chat,[]),day,gain,detail_limit=None)
        parts.append('<b>'+title+'</b>\n'+text)
    # Preserve legacy admin-only trades without assigning them to a public channel.
    assigned={t['id'] for group in groups.values() for t in group}
    other=[t for t in trades if t['id'] not in assigned]
    if other:
        text,_=daily_report_text(other,day,gain,detail_limit=None)
        parts.append('<b>الإدارة / غير منشورة بالقناتين</b>\n'+text)
    parts.append('الصفقة المنشورة بالقناتين تظهر في كل قسم؛ لا تُجمع نتائج القسمين كإجمالي فريد.')
    return '\n\n'.join(parts)


def weekly_text(trades, friday, gain):
    text,payload=daily_report_text(trades,friday,gain,detail_limit=None)
    text=text.replace('البيان اليومي','البيان الأسبوعي',1)
    monday=friday-timedelta(days=4)
    lines=text.splitlines()
    lines[1]=f'{monday.isoformat()} ← {friday.isoformat()} · جلسات نيويورك'
    return '\n'.join(lines),payload


async def send_pages(rt, entity, ident, event, text, chat=None, payload=None):
    from . import queries as q
    frozen=await rt.db.fetchrow("""INSERT INTO report_snapshots(entity_type,entity_id,event_type,body)
        VALUES($1,$2,$3,$4) ON CONFLICT(entity_type,entity_id,event_type)
        DO UPDATE SET body=report_snapshots.body RETURNING body""",entity,ident,event,text)
    text=frozen['body']
    for index,part in enumerate(pages(text)):
        await rt.publisher.text_once(entity,ident,event,part,level=index,chat_id=chat,payload=payload)
        state=await rt.db.fetchrow(q.EVENT_STATUS,entity,ident,event,index)
        if not state or state['status']!='SENT':return False
    return True

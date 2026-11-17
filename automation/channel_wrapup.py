"""Ordered, durable daily -> weekly (Friday) -> greeting, per channel."""
from datetime import timedelta
from . import queries as q, channel_routing as routing
from .daily_report import daily_report_text
from .report_sections import send_pages, weekly_text

WEEKLY_GREETING = ('🦋 <b>QUIET ALPHA | ختام الأسبوع</b>\n\n'
 'نختتم أسبوعنا بحمد الله، ونسأله التوفيق دائمًا وأبدًا.\n\n'
 'نثمّن ثقتكم ووجودكم معنا في هذه الرحلة 🤍\n\n'
 'لكل أسبوع فرصه وتحدياته، ومن كل نتيجة نتعلّم ونراجع ونطوّر.\n\n'
 'نلتقي الأسبوع القادم بإذن الله.\n\n'
 '<b>إجازة سعيدة لكم، وفي أمان الله 🕊️</b>')

async def send(rt, day, chat, group, original, manual=False):
    if any(t['status']=='TRACKING' for t in group):return False
    if not await rt.session_finish.cards_sent(original,chat):return False
    ident=int(day.strftime('%Y%m%d'))
    revision=max((t['id'] for t in group),default=0)
    event='DAILY_REPORT_'+str(chat)
    if manual:event+='_R'+str(revision)
    text,payload=daily_report_text(group,day,rt.settings.tracking.success_premium_gain,detail_limit=None)
    if manual:text+='\n\nيشمل صفقات اليوم حتى الآن؛ يُحدّث بعد إغلاق أي عقد جديد.'
    if not await send_pages(rt,'daily',ident,event,text,chat,payload):return False
    if day.weekday()==4:
        weekly=[]; raw=[]
        for offset in range(4,-1,-1):
            rows=await rt.db.fetch(q.DAILY_TRADES,day-timedelta(days=offset))
            views=routing.report_groups(rows).get(chat,[])
            ids={t['id'] for t in views}
            weekly.extend(views);raw.extend(t for t in rows if t['id'] in ids)
        if any(t['status']=='TRACKING' for t in weekly):return False
        if not await rt.session_finish.cards_sent(raw,chat):return False
        text,payload=weekly_text(weekly,day,rt.settings.tracking.success_premium_gain)
        weekly_event='WEEKLY_REPORT_'+str(chat)
        if manual:weekly_event+='_R'+str(max((t['id'] for t in weekly),default=0))
        if not await send_pages(rt,'weekly_report',ident,weekly_event,text,chat,payload):return False
        greeting=WEEKLY_GREETING
        ending='WEEKLY_GREETING_'+str(chat)
    else:
        from .closing_message import CAPTION
        from .session_finish import CAPTION as FINISH
        greeting=FINISH if manual else CAPTION
        ending='FINISH_GREETING_'+str(chat)
    if manual:ending+='_R'+str(revision)
    return await send_pages(rt,'daily',ident,ending,greeting,chat)

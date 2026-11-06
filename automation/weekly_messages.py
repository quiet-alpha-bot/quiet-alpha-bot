"""Durable Monday greetings; instructions unlock only after welcome delivery."""
import asyncio
import logging
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from . import queries as q, channel_routing as routing
from .opening_prayer import destinations
from .timeutil import utcnow

log = logging.getLogger('quiet-alpha.weekly-messages')
RIYADH = ZoneInfo('Asia/Riyadh')
WELCOME = '''🦋 <b>QUIET ALPHA | WELCOME</b>

<b>بسم الله الرحمن الرحيم</b>

لكل نجاح أساس، ولكل استراتيجية منهج، ولكل قرار مدروس قيمة.

يسرّنا أن نرحب بكم في <b>Quiet Alpha</b>، حيث نؤمن أن التميز في الأسواق المالية لا يُقاس بكثرة الفرص، بل بجودة الاختيار، ودقة التحليل، والانضباط في اتخاذ القرار.

نشارككم تحليل عقود <b>SPX</b> ومتابعة أدائها وتوثيق نتائجها، ضمن منهجية تقوم على التحليل وإدارة المخاطر، وانتظار الفرص التي تستوفي معايير استراتيجيتنا.

نواصل هذه الرحلة ضمن مرحلة اختبار وتقييم مستمرة، بهدف تطوير الاستراتيجية وقياس أدائها في ظروف السوق الحقيقية.

<b>رؤيتنا أن تكون لكل صفقة أسباب واضحة، ولكل قرار خطة، ولكل نتيجة قيمة نتعلم منها.</b>

نرحب بالمنضمين حديثًا، ونثمّن ثقة كل من يرافقنا في هذه الرحلة.

نسأل الله أن يكتب لنا ولكم التوفيق والسداد، وأن يجعل هذا العمل نافعًا للجميع.

<b>أهلًا بكم في Quiet Alpha 🤍🦋</b>'''
INSTRUCTIONS = '''🦋 <b>QUIET ALPHA | تعليمات متابعة عقود SPX</b>

حرصًا على وضوح آلية العمل وتنظيم متابعة الصفقات، نرجو الاطلاع على التعليمات التالية:

🟢 <b>الدخول في الصفقات</b>

تُعتمد الصفقات من خلال بطاقة الدخول الرسمية فقط، وأي تعليق أو تحديث خارج البطاقة لا يُعتبر إشارة دخول جديدة.

🎯 <b>هدف العقد ومعيار النجاح</b>

الهدف المعتمد هو ارتفاع بقيمة <b>+50$ للعقد الواحد</b> عن سعر الدخول المنشور، قبل الرسوم والعمولات، ويُسجّل العقد ناجحًا عند رصد بلوغ هذا الهدف.

🔐 <b>تأمين الأرباح</b>

عند تحقيق الهدف، تُعد هذه منطقة لتأمين الأرباح، ويكون قرار الاستمرار في الصفقة وفق خطة كل متداول وإدارته للمخاطر.

🛑 <b>وقف الخسارة</b>

وقف الخسارة المعتمد للقناة هو <b>35% من قيمة العقد عند الدخول</b>، مع مراعاة اختلاف رأس المال والقدرة على تحمل المخاطر بين المتداولين.

🔄 <b>العودة إلى سعر الدخول</b>

تُغلق متابعة العقد عند بلوغ وقف الخسارة؛ ولا يستمر تتبعه انتظارًا للتعافي بعد الوقف.

• إذا حقق العقد هدف <b>+50$ أو أكثر</b> ثم عاد إلى سعر الدخول، تُغلق متابعته مع الاحتفاظ بتصنيفه صفقة ناجحة.

🚫 <b>الدخول المتأخر</b>

لا تتم مطاردة العقود بعد ابتعادها عن سعر الدخول المنشور، لتجنب الدخول بمخاطرة أعلى.

📊 <b>تحديثات العقود</b>

جميع التحديثات المنشورة مخصصة لمتابعة أداء العقد، ولا تُعد إشارات دخول جديدة.

الأسعار والنتائج المنشورة مبنية على رصد البوت، وقد تختلف عن أسعار التنفيذ الفعلية لدى المتداول. وتسجيل العقد ناجحًا يعني بلوغه الهدف المرصود، ولا يعني أن كل متداول حقق الربح نفسه.

💰 <b>إدارة رأس المال</b>

يختلف حجم العقود ومستوى المخاطرة من متداول لآخر، ويظل تحديدهما مسؤولية كل متداول وفق خطته الخاصة.

📰 <b>الأخبار والتذبذب</b>

قد نفضّل الانتظار خلال الأخبار المؤثرة أو التذبذب المرتفع حتى تتضح حركة السوق.

━━━━━━━━━━━━

⚠️ <b>تنويه مهم</b>

جميع التحليلات والعقود المنشورة في <b>Quiet Alpha</b> مخصصة للأغراض التعليمية واختبار الاستراتيجية وتقييم أدائها، ولا تُعد توصيات استثمارية أو دعوات مباشرة للشراء أو البيع.

تداول عقود الخيارات ينطوي على مخاطر مرتفعة قد تصل إلى خسارة كامل المبلغ المستثمر، وتبقى قرارات الدخول والخروج وإدارة رأس المال مسؤولية كل متداول.

<b>نختار فرصنا بعناية، وندير مخاطرنا بانضباط 🦋</b>

<b>QUIET ALPHA 🦋</b>'''
INSERT = '''INSERT INTO weekly_messages(week_date,kind,caption,public_chat_id,private_chat_id)
VALUES($1,$2,$3,$4,$5) ON CONFLICT(week_date,kind) DO NOTHING'''
GET = 'SELECT * FROM weekly_messages WHERE id=$1'
OPEN = "SELECT * FROM weekly_messages WHERE status IN ('PENDING','APPROVED') ORDER BY week_date,id"
DECIDE = '''UPDATE weekly_messages SET status=$2,publication_choice=$3,decided_by=$4,decided_at=$5
WHERE id=$1 AND status='PENDING' RETURNING *'''
DONE = "UPDATE weekly_messages SET status='SENT' WHERE id=$1 AND status='APPROVED'"


def due_day(now):
    local = now.astimezone(RIYADH)
    return local.date() if local.weekday() == 0 and local.hour >= 12 else None


def keyboard(ident):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f'qaw:{action}:{ident}')
         for label, action in [('🌐 العامة','public'),('🔒 الخاصة','private')]],
        [InlineKeyboardButton(label, callback_data=f'qaw:{action}:{ident}')
         for label, action in [('📣 الاثنتان','both'),('❌ رفض','skip')]]])


class WeeklyMessages:
    def __init__(self, runtime):
        self.rt = runtime
        self.last_error = None

    async def run(self):
        while True:
            try:
                await self.tick()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = type(error).__name__
                log.exception('Weekly message cycle failed')
            await asyncio.sleep(15)

    async def insert(self, day, kind, caption, public, private):
        await self.rt.db.execute(INSERT, day, kind, caption, public, private)

    async def tick(self):
        rt = self.rt
        if not routing.enabled() or rt.settings.owner_user_id is None:return
        day = due_day(utcnow())
        if day:
            await self.insert(day, 'WELCOME', WELCOME, routing.PUBLIC_CHAT, routing.PRIVATE_CHAT)
        for row in await rt.db.fetch(OPEN):
            if row['status'] == 'PENDING':await self.preview(row)
            else:await self.publish(row)

    async def preview(self, row):
        await self.rt.publisher.text_once('weekly_message',row['id'],'PREVIEW',
            row['caption']+'\n\nمعاينة الإدارة — اختاري العامة أو الخاصة أو الاثنتين أو رفض.\n'
            'تبقى الموافقة متاحة حتى النشر أو الرفض.',reply_markup=keyboard(row['id']))

    async def publish(self, row):
        if row['status'] != 'APPROVED':return
        rt = self.rt
        for event, chat in destinations(row):
            await rt.publisher.text_once('weekly_message',row['id'],event,row['caption'],chat_id=chat)
            state = await rt.db.fetchrow(q.EVENT_STATUS,'weekly_message',row['id'],event,0)
            if not state or state['status'] != 'SENT':return
        # Insert next stage before completing this one, so a restart cannot lose it.
        if row['kind'] == 'WELCOME':
            await self.insert(row['week_date'],'INSTRUCTIONS',INSTRUCTIONS,
                              row['public_chat_id'],row['private_chat_id'])
        await rt.db.execute(DONE,row['id'])

    async def callback(self, update, context):
        query = update.callback_query
        if query is None:return
        try:await self._callback(query)
        except Exception as error:
            self.last_error = type(error).__name__
            log.exception('Weekly message approval failed')
            try:await query.answer('تعذر إكمال الطلب؛ راجعي حالة البوت.',show_alert=True)
            except Exception:pass

    async def _callback(self, query):
        rt = self.rt
        if query.from_user is None or query.from_user.id != rt.settings.owner_user_id:
            await query.answer('⛔ غير مصرح',show_alert=True);return
        if not rt.ready or not routing.enabled():
            await query.answer('الرسائل غير جاهزة حاليًا.',show_alert=True);return
        if query.message is None or str(query.message.chat.id) != str(rt.settings.signal_chat_id):
            await query.answer('استخدمي معاينة الإدارة.',show_alert=True);return
        try:
            prefix, action, ident = query.data.split(':');ident = int(ident)
            if prefix != 'qaw' or action not in ('public','private','both','skip'):raise ValueError
        except (ValueError,TypeError):
            await query.answer('طلب غير صالح.',show_alert=True);return
        row = await rt.db.fetchrow(GET,ident)
        if row is None:
            await query.answer('الرسالة غير موجودة.',show_alert=True);return
        if action != 'skip':
            await routing.check_permission(rt.app.bot,routing.destination(action),rt.settings.signal_chat_id)
        row = await rt.db.fetchrow(DECIDE,ident,'SKIPPED' if action=='skip' else 'APPROVED',
                                   'both' if action=='skip' else action,query.from_user.id,utcnow())
        if row is None:
            await query.answer('تم تسجيل القرار مسبقًا.',show_alert=True);return
        await query.answer('تم رفض الرسالة.' if action=='skip' else 'تم اعتماد النشر.')
        try:await query.edit_message_reply_markup(reply_markup=None)
        except Exception:pass
        if action != 'skip':
            await self.publish(row)
            await self.tick()

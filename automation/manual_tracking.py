"""Owner-only manual trades: durable preview, explicit confirmation, same tracker."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import queries as q
from . import reconsideration
from . import channel_routing as routing
from .milestones import milestone_level, new_milestones, is_success
from .occ import OccSymbol, parse_occ, strike_text
from .timeutil import utcnow, et_date, et_datetime, within_session
from .tracker import hard_stop_price

logger = logging.getLogger('quiet-alpha.manual')
PREVIEW_SECONDS = 300
USAGE = (
    'لإضافة عقد يدوي للتتبع:\n'
    '/qa_track SPXW 7770 PUT 3.10\n\n'
    'الترتيب: الرمز، الاسترايك، CALL أو PUT، سعر دخولك.\n'
    'الانتهاء افتراضيًا اليوم، وبعد الإغلاق أقرب يوم من الاثنين إلى الجمعة. راجعي عطلات السوق أو حدديه:\n'
    '/qa_track SPXW 7770 PUT 2026-10-26 3.10\n\n'
    'تظهر معاينة أولًا، ولا يبدأ النشر والتتبع إلا بعد التأكيد.'
)


REFRESH_PRICE = """UPDATE proposals SET reconfirm_price=$2, updated_at=now()
WHERE id=$1 AND classification='MANUAL' AND status='PENDING_APPROVAL' AND expires_at>now()
AND reconfirm_price IS NOT DISTINCT FROM $3 RETURNING *"""

class PriceChanged(ValueError):
    def __init__(self, proposal, price):
        self.proposal, self.price = proposal, price
        super().__init__('تغيّر السعر؛ راجعي السعر الجديد ثم اختاري قناة النشر للتأكيد.')

def manual_keyboard(pid, price):
    options = [('🌐 العامة','public'),('🔒 الخاصة','private'),('📣 الاثنتان','both')] if routing.enabled() else [('✅ اعتماد وبدء التتبع','start')]
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f'qam:{action}:{pid}:{price:.2f}')
        for label,action in options
    ] + [InlineKeyboardButton('❌ رفض',callback_data=f'qam:cancel:{pid}')]])


def positive_number(value):
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError('الاسترايك وسعر الدخول يجب أن يكونا أرقامًا صحيحة.') from None
    if not result.is_finite() or result <= 0 or result >= Decimal('100000'):
        raise ValueError('الاسترايك وسعر الدخول يجب أن يكونا موجبين وأقل من 100000.')
    return result


def parse_manual_args(args, today, root):
    if len(args) == 2:
        occ = parse_occ(args[0])
        entry = positive_number(args[1])
    elif len(args) in (4, 5):
        symbol_root, strike, side = args[:3]
        strike = positive_number(strike)
        if strike * 1000 != (strike * 1000).to_integral_value():
            raise ValueError('الاسترايك لا يقبل أكثر من 3 منازل عشرية.')
        side = side.upper()
        if side not in ('CALL', 'PUT'):
            raise ValueError('نوع العقد يجب أن يكون CALL أو PUT.')
        try:
            expiry = date.fromisoformat(args[3]) if len(args) == 5 else today
        except ValueError:
            raise ValueError('صيغة تاريخ الانتهاء: YYYY-MM-DD.') from None
        occ = OccSymbol(symbol_root.upper(), expiry, side, strike)
        entry = positive_number(args[-1])
    else:
        raise ValueError(USAGE)
    if occ is None or occ.root != root or occ.strike <= 0:
        raise ValueError(f'هذا التتبع يدعم عقود {root} فقط. راجعي بيانات العقد.')
    if occ.expiry < today:
        raise ValueError('العقد منتهي؛ اختاري تاريخ انتهاء حاليًا أو لاحقًا.')
    if entry != entry.quantize(Decimal('.01')):
        raise ValueError('اكتبي سعر الدخول بمنزلتين عشريتين كحد أقصى.')
    return occ, entry


def validate_quote(quote, symbol, now):
    if quote is None or quote.symbol != symbol:
        raise ValueError('لم يصل سعر لهذا العقد من UW؛ لم يبدأ التتبع.')
    bid, ask = quote.bid, quote.ask
    if (bid is None or ask is None or not bid.is_finite() or not ask.is_finite()
        or bid <= 0 or ask < bid):
        raise ValueError('سعر Bid/Ask غير صالح؛ لم يبدأ التتبع.')
    if (quote.last_tape_time is None
        or not 0 <= (now - quote.last_tape_time).total_seconds() <= 120):
        raise ValueError('بيانات العقد غير حديثة؛ أعيدي المحاولة عند وصول بيانات حديثة.')


class ManualTracking:
    def __init__(self, runtime):
        self.rt = runtime

    async def command(self, update, context):
        if not await self.rt._guard(update):
            return
        if update.effective_chat is None or update.effective_chat.type != 'private':
            await update.message.reply_text('أرسلي الأمر في خاص البوت لتبقى المعاينة عندك.')
            return
        if not context.args:
            await update.message.reply_text(USAGE)
            return
        try:
            now = utcnow()
            default_day = et_date(now)
            if now >= et_datetime(default_day, self.rt.settings.market.close_et):
                default_day += timedelta(days=1)
            while default_day.weekday() >= 5:
                default_day += timedelta(days=1)
            occ, entry = parse_manual_args(context.args, default_day, self.rt.settings.uw.contract_root)
            self._available()
            db = self.rt.db
            async with db.pool.acquire() as conn:
                async with conn.transaction():
                    signal = await conn.fetchrow(q.INSERT_MANUAL_SIGNAL,
                        f'manual:{update.effective_chat.id}:{update.message.message_id}',
                        occ.right, self.rt.settings.uw.spot_ticker, now,
                        {'owner_id': update.effective_user.id, 'chat_id': update.effective_chat.id})
                    proposal = await conn.fetchrow(q.INSERT_PROPOSAL,
                        signal['id'], None, occ.right, occ.symbol, occ.strike, occ.expiry,
                        entry, None, None, None, 'MANUAL', None,
                        {'manual': True, 'owner_id': update.effective_user.id,
                         'preview_chat_id': update.effective_chat.id}, now + timedelta(seconds=PREVIEW_SECONDS))
                    if proposal is None:
                        proposal = await conn.fetchrow(q.GET_PROPOSAL_BY_SIGNAL, signal['id'])
            if proposal['status'] != 'PENDING_APPROVAL' or proposal['expires_at'] <= utcnow():
                await update.message.reply_text('هذا الطلب تمت معالجته أو انتهت صلاحيته. أرسلي أمرًا جديدًا.')
                return
            occ = parse_occ(proposal['option_symbol'])
            entry = Decimal(proposal['suggested_entry'])
            pid = proposal['id']
            keyboard = manual_keyboard(pid, entry)
            # Do not store a private message id in proposals.message_id: existing
            # maintenance edits that field using the publishing channel's chat id.
            await update.message.reply_text(
                '🦋 <b>QUIET ALPHA — عقد يدوي</b>\n\n'
                f'<b>{escape(occ.root)} · {strike_text(occ.strike)} {occ.right}</b>\n'
                f'الانتهاء: <b>{occ.expiry.isoformat()}</b>\n'
                f'سعر دخولك: <b>${entry:.2f}</b>\n'
                f'مستوى النجاح: <b>${entry + self.rt.settings.tracking.success_premium_gain:.2f}</b>\n\n'
                'وقت الجلسة نراجع سعر الشراء الحالي قبل الاعتماد؛ إذا تغيّر نعرضه لتأكيدك.\n'
                'الأرقام تُرصد من بدء المتابعة، ولا تشمل حركة العقد السابقة.\n'
                'راجعي تاريخ الانتهاء أعلاه؛ خارج الجلسة يبدأ الرصد عند وصول سعر حي، دون احتساب أسعار سابقة.\n'
                'المعاينة صالحة لمدة 5 دقائق.', parse_mode='HTML', reply_markup=keyboard)
        except ValueError as error:
            await update.message.reply_text(str(error))
        except Exception:
            logger.exception('Manual trade preview failed')
            await update.message.reply_text('تعذر تجهيز المعاينة؛ لم يبدأ تتبع جديد. راجعي السجل.')

    def _available(self):
        rt = self.rt
        if not (rt.ready and rt.is_leader and rt.db and rt.uw and rt.tracker and rt.settings.flags.tracking):
            raise ValueError('التتبع غير جاهز حاليًا؛ أعيدي المحاولة بعد اكتمال تشغيل البوت.')

    def _session(self, proposal, now):
        s = self.rt.settings
        if now >= et_datetime(proposal['expiry'], s.market.close_et):
            raise ValueError('العقد منتهي. حددي تاريخ انتهاء قادم عبر /qa_track.')

    async def confirm(self, proposal_id, user_id, chat_id, publication_chat_id=None, displayed_price=None):
        """Atomically approve and register; no Telegram I/O inside the transaction."""
        self._available()
        rt = self.rt
        proposal = await rt.db.fetchrow(q.GET_PROPOSAL, proposal_id)
        self._check_request(proposal, user_id, chat_id)
        if proposal['status'] == 'APPROVED':
            return await rt.db.fetchrow(q.TRADE_BY_PROPOSAL, proposal_id), False
        if routing.enabled() and publication_chat_id is None:
            raise ValueError('اختاري العامة أو الخاصة؛ أعيدي إرسال الأمر إن كانت المعاينة قديمة.')
        if publication_chat_id:
            await routing.check_permission(rt.app.bot,publication_chat_id,rt.settings.signal_chat_id)
        if proposal['status'] != 'PENDING_APPROVAL' or proposal['expires_at'] <= utcnow():
            raise ValueError('انتهت صلاحية الطلب أو تمت معالجته؛ أرسلي طلبًا جديدًا.')
        self._session(proposal, utcnow())
        quote = None
        now = utcnow()
        if within_session(now, rt.settings.market.open_et, rt.settings.market.close_et):
            quotes, _ = await rt.uw.quotes_for([proposal['option_symbol']])
            quote = quotes.get(proposal['option_symbol'])
            now = utcnow()
            validate_quote(quote, proposal['option_symbol'], now)
        entry = Decimal(proposal['suggested_entry'])
        reference = Decimal(proposal.get('reconfirm_price') or entry)
        displayed = Decimal(str(displayed_price)) if displayed_price is not None else Decimal(proposal['suggested_entry'])
        if not displayed.is_finite() or displayed != reference:
            raise PriceChanged(proposal, reference)
        if quote is not None:
            current = quote.ask
            if not rt.settings.selection.premium_min <= current <= rt.settings.selection.premium_max:
                raise ValueError('السعر الحالي خارج نطاق الدخول المحدد؛ لم تُعتمد الصفقة.')
            if current > Decimal(proposal['suggested_entry']) + rt.settings.approval.confirmation_tolerance_abs:
                raise ValueError('السعر ارتفع أكثر من السماح المحدد ($0.30) عن سعرك الأصلي؛ أرسلي طلبًا جديدًا إذا رغبتِ.')
            if current != reference:
                updated = await rt.db.fetchrow(REFRESH_PRICE, proposal_id, current, proposal.get('reconfirm_price'))
                if updated is None:
                    raise ValueError('تغيّرت حالة الطلب؛ أعيدي فتح المعاينة قبل التأكيد.')
                raise PriceChanged(updated, current)
            entry = current
        else:
            entry = reference
        if not rt.settings.selection.premium_min <= entry <= rt.settings.selection.premium_max:
            raise ValueError('سعر الدخول خارج النطاق المحدد؛ لم تُعتمد الصفقة.')
        if quote is not None and quote.bid <= hard_stop_price(entry, rt.settings.tracking.stop_loss_pct):
            raise ValueError('السعر الحالي وصل إلى وقف الخسارة بالنسبة لدخولك؛ لم يبدأ تتبع جديد.')
        async with rt.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(q.LOCK_TRADE_DIRECTION, proposal['direction'])
                proposal = await conn.fetchrow(q.LOCK_MANUAL_PROPOSAL, proposal_id)
                self._check_request(proposal, user_id, chat_id)
                if proposal['status'] == 'APPROVED':
                    return await conn.fetchrow(q.TRADE_BY_PROPOSAL, proposal_id), False
                if Decimal(proposal.get('reconfirm_price') or proposal['suggested_entry']) != reference:
                    raise PriceChanged(proposal, Decimal(proposal.get('reconfirm_price') or proposal['suggested_entry']))
                now = utcnow()
                self._session(proposal, now)
                if quote is not None:
                    validate_quote(quote, proposal['option_symbol'], now)
                if proposal['status'] != 'PENDING_APPROVAL' or proposal['expires_at'] <= now:
                    raise ValueError('انتهت صلاحية الطلب أو تمت معالجته؛ أرسلي طلبًا جديدًا.')
                if await conn.fetchval(q.OPEN_DIRECTION_STRIKE_EXISTS, proposal['direction'], proposal['strike']):
                    raise ValueError('يوجد عقد مفتوح بنفس الاسترايك والاتجاه؛ لم تُسجل صفقة مكررة.')
                if await conn.fetchval(q.COUNT_TRACKING_DIRECTION, proposal['direction']) >= 6:
                    raise ValueError('وصلت الصفقات المفتوحة في هذا الاتجاه إلى 6؛ لم تُضف صفقة جديدة.')
                if publication_chat_id:
                    await conn.execute(routing.SET_MANUAL_CHANNEL,proposal_id,publication_chat_id)
                if await conn.fetchval(q.APPROVE_MANUAL_PROPOSAL, proposal_id, user_id) is None:
                    raise ValueError('انتهت صلاحية الطلب؛ أرسلي طلبًا جديدًا.')
                signal = await conn.fetchrow(q.GET_SIGNAL, proposal['signal_db_id'])
                trade = await conn.fetchrow(q.INSERT_TRADE, proposal_id, signal['id'], signal['signal_id'],
                    proposal['option_symbol'], proposal['direction'], proposal['strike'], proposal['expiry'],
                    Decimal(proposal['suggested_entry']), entry, quote.as_record() if quote is not None else {'awaiting_live_quote': True},
                    'MANUAL', None, now, now, quote.bid if quote else None,
                    quote.ask if quote else None, quote.last if quote else None)
                if trade is None:
                    raise RuntimeError('Manual approval has no trade')
                if quote is not None:
                    # Preserve the first verified observation; no historical price backfill.
                    await conn.execute(q.UPDATE_TRADE_QUOTE, trade['id'], quote.bid, quote.ask, quote.last,
                        quote.bid, now, quote.mid, quote.last, quote.bid, now, now, quote.last_tape_time)
                    tracking = rt.settings.tracking
                    if rt.settings.flags.milestones:
                        reached = milestone_level(entry, quote.bid, tracking.milestone_step_pct)
                        levels = new_milestones(0, reached, tracking.milestone_step_pct)
                        if levels:
                            await conn.execute(q.RECORD_MILESTONE_DETECTIONS, trade['id'], reached,
                                               {str(level): str(quote.bid) for level in levels})
                    if rt.settings.flags.success_card and is_success(entry, quote.bid, tracking.success_premium_gain):
                        await conn.fetchval(q.MARK_SUCCESS_DETECTED, trade['id'], now, quote.bid)
                trade = await conn.fetchrow(q.GET_TRADE, trade['id'])
        return trade, True

    @staticmethod
    def _check_request(proposal, user_id, chat_id):
        if (proposal is None or proposal['classification'] != 'MANUAL'
            or proposal['details'].get('owner_id') != user_id
            or proposal['details'].get('preview_chat_id') != chat_id):
            raise ValueError('طلب غير صالح أو ليس تابعًا لهذه المحادثة.')

    async def callback(self, update, context):
        query = update.callback_query
        if query is None:
            return
        if not self.rt._is_owner(update):
            await query.answer('غير مصرح.', show_alert=True)
            return
        if query.message is None or query.message.chat.type != 'private':
            await query.answer('استخدمي المعاينة في خاص البوت.', show_alert=True)
            return
        try:
            parts = query.data.split(':')
            if len(parts) not in (3,4):raise ValueError('طلب غير صالح.')
            _, action, pid = parts[:3]
            displayed_price = parts[3] if len(parts)==4 else None
            pid = int(pid)
            if action not in ('start', 'cancel', 'public', 'private', 'both', 'reopen'):
                raise ValueError('إجراء غير صالح.')
            await query.answer('جارٍ التحقق…')
            user_id, chat_id = update.effective_user.id, query.message.chat.id
            if action == 'reopen':
                proposal = await self.rt.db.fetchrow(q.GET_PROPOSAL,pid)
                self._check_request(proposal,user_id,chat_id)
                row = await reconsideration.restore(self.rt.db,pid,user_id)
                if row is None:raise ValueError('انتهت مهلة إعادة النظر أو تمت معالجة الطلب.')
                price=Decimal(row.get('reconfirm_price') or row['suggested_entry'])
                await query.edit_message_text(
                    f"🦋 عاد العقد اليدوي للمراجعة\n{row['option_symbol']}\nسعر الدخول المعروض: ${price:.2f}\n"
                    "اختاري قناة النشر؛ يُفحص السعر مجددًا قبل الاعتماد. مهلة الرفض الأصلية لم تتغير.",
                    reply_markup=manual_keyboard(pid,price))
                return
            if action == 'cancel':
                proposal = await self.rt.db.fetchrow(q.GET_PROPOSAL, pid)
                self._check_request(proposal, user_id, chat_id)
                changed = await reconsideration.reject(self.rt.db,pid,'PENDING_APPROVAL',user_id)
                if changed is None:
                    raise ValueError('تمت معالجة الطلب بالفعل؛ الإلغاء هنا لا يغلق صفقة مفتوحة.')
                await query.edit_message_text('رُفض العقد اليدوي، ولم تُفتح صفقة. يمكنك إعادة النظر خلال ١٥ دقيقة من الآن.',
                    reply_markup=reconsideration.keyboard(pid,manual=True))
                return
            if action in ('public','private','both') and not routing.enabled():
                raise ValueError('اختيار القنوات غير مفعّل.')
            chosen = routing.destination(action) if action in ('public','private','both') else None
            trade, created = await self.confirm(pid, user_id, chat_id,chosen,displayed_price)
            if trade is None:
                raise RuntimeError('Approved manual request has no trade')
            # Durable registration precedes delivery. Leader tick retries failed
            # entry-card delivery; never create another trade on a send failure.
            try:
                if trade['status'] == 'TRACKING':
                    await self.rt.tracker.ensure_started(trade)
            except Exception:
                logger.exception('Manual trade registered; entry delivery will retry')
            await query.edit_message_text(
                f"✅ {'تم تسجيل' if created else 'سبق تسجيل'} العقد اليدوي — صفقة #{trade['id']}\n"
                f"{trade['option_symbol']}\nسعر الدخول: ${Decimal(trade['approved_entry']):.2f}\n"
                f"قناة النشر: {routing.channel_name(trade.get('publication_chat_id'))}. "
                'تابعي حالة التتبع عبر /qa_status.')
        except PriceChanged as error:
            await query.edit_message_text(
                f"🦋 تحديث سعر العقد اليدوي\n{error.proposal['option_symbol']}\n"
                f"السعر الأصلي: ${Decimal(error.proposal['suggested_entry']):.2f}\n"
                f"سعر الدخول المعروض: ${error.price:.2f}\n\n"
                "لم تُفتح صفقة بعد. اختاري القناة مجددًا لاعتماد السعر بعد فحصه؛ خارج الجلسة يُستخدم السعر المعروض.\n"
                "صلاحية الطلب الأصلية لم تتغير.",
                reply_markup=manual_keyboard(error.proposal['id'],error.price))
        except ValueError as error:
            await query.message.reply_text(str(error))
        except Exception:
            logger.exception('Manual trade callback failed')
            await query.message.reply_text('تعذر إكمال الطلب. راجعي /qa_status قبل إرسال أمر جديد؛ يمكنك إعادة الضغط على نفس الطلب دون تكرار الصفقة.')

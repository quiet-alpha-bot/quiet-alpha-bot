"""Owner-requested immediate close at a newly fetched, fresh observed Bid."""
import logging
from decimal import Decimal
from . import queries as q
from .timeutil import utcnow, within_session

logger = logging.getLogger('quiet-alpha.manual-exit')
OWNER_REASONS = frozenset({'OWNER_EXIT', 'OWNER_EXIT_EXCLUDED', 'OWNER_EXIT_FLAT'})


def exit_reason(trade, bid, gain):
    entry = Decimal(trade['approved_entry'])
    peak = max(Decimal(trade['highest_bid']) if trade['highest_bid'] is not None else bid, bid)
    succeeded = trade['success_detected_at'] is not None or peak >= entry + gain
    if succeeded:
        return 'OWNER_EXIT'
    if bid > entry:
        return 'OWNER_EXIT_EXCLUDED'
    return 'OWNER_EXIT_FLAT' if bid == entry else 'OWNER_EXIT'


class ManualExit:
    def __init__(self, runtime):
        self.rt = runtime

    async def close(self, trade_id):
        rt = self.rt
        trade = await rt.db.fetchrow(q.GET_TRADE, trade_id)
        if trade is None:
            raise ValueError('لا توجد صفقة بهذا الرقم.')
        if trade['status'] != 'TRACKING':
            raise ValueError('الصفقة مغلقة بالفعل؛ لم يتغير سعرها أو نتيجتها.')
        now = utcnow()
        if not within_session(now, rt.settings.market.open_et, rt.settings.market.close_et):
            raise ValueError('السوق خارج الجلسة؛ لا يمكن تسجيل إغلاق بسعر قديم.')
        if rt.uw is None:
            raise ValueError('مصدر الأسعار غير متاح؛ لم تُوقف الصفقة.')
        quotes, response = await rt.uw.quotes_for([trade['option_symbol']])
        now = utcnow()
        quote = quotes.get(trade['option_symbol'])
        if (quote is None or quote.symbol != trade['option_symbol']
            or quote.bid is None or not quote.bid.is_finite() or quote.bid < 0
            or quote.ask is None or not quote.ask.is_finite() or quote.ask < quote.bid
            or quote.last_tape_time is None
            or not 0 <= (now - quote.last_tape_time).total_seconds() <= 120
            or not within_session(now, rt.settings.market.open_et, rt.settings.market.close_et)):
            raise ValueError('تعذر الحصول على سعر حديث وصالح؛ لم تُوقف الصفقة. أعيدي المحاولة.')
        gain = rt.settings.tracking.success_premium_gain
        async with rt.db.pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchrow(q.LOCK_EXIT_TRADE, trade_id)
                if current is None or current['status'] != 'TRACKING':
                    raise ValueError('أُغلقت الصفقة أثناء جلب السعر؛ لم يتغير سعر إغلاقها.')
                if current['last_poll_at'] is not None and current['last_poll_at'] > now:
                    raise ValueError('وصل تحديث أحدث للعقد؛ أعيدي أمر الإيقاف للحصول على أحدث سعر.')
                reason = exit_reason(current, quote.bid, gain)
                closed = await conn.fetchrow(q.OWNER_CLOSE_TRADE, trade_id, quote.bid,
                    quote.ask, quote.last, now, quote.last_tape_time, reason, gain)
                await conn.execute(q.INSERT_PRICE_OBSERVATION, trade_id, now, quote.bid,
                    quote.ask, quote.last, quote.volume, quote.open_interest,
                    quote.last_tape_time, response.latency_ms if response else None, True, None)
        return closed

    async def command(self, update, context):
        rt = self.rt
        if not await rt._guard(update):
            return
        if len(context.args) != 1 or not context.args[0].isascii() or not context.args[0].isdigit() or not 0 < int(context.args[0]) < 2**63:
            await update.message.reply_text('الاستخدام: /qa_exit 231\n231 هو رقم الصفقة Trade، وليس رقم المقترح Proposal.')
            return
        try:
            closed = await self.close(int(context.args[0]))
        except ValueError as error:
            await update.message.reply_text(str(error))
            return
        except Exception:
            logger.exception('Owner exit could not be confirmed')
            await update.message.reply_text('تعذر تأكيد تنفيذ الإيقاف. راجعي /qa_status قبل إعادة المحاولة.')
            return
        # Closure is committed before delivery; a delivery failure cannot reopen it.
        try:
            await rt.tracker.refresh_message(closed['id'], closed_reason=closed['close_reason'])
            sent = await rt.tracker.send_public_exit(closed)
            if rt.settings.flags.highest_card:
                await rt.tracker.send_highest_card(closed)
        except Exception:
            logger.exception('Owner exit delivery failed for trade #%s', closed['id'])
            sent = None
        result = (Decimal(closed['current_bid']) - Decimal(closed['approved_entry'])) * 100
        stats = 'مستبعدة من البيان.' if closed['close_reason'] == 'OWNER_EXIT_EXCLUDED' else 'ستُحتسب في البيان.'
        await update.message.reply_text(
            f"تم إيقاف الصفقة #{closed['id']} عند Bid ${closed['current_bid']:.2f}.\n"
            f"الفرق عن الدخول: ${result:+.2f} للعقد. {stats}\n"
            + ('أُرسلت رسالة الإغلاق.' if sent is not None else 'إرسال رسالة الإغلاق يحتاج التحقق؛ الإيقاف محفوظ.'))

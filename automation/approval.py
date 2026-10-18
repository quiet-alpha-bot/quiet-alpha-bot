"""APPROVE / REJECT / RECONFIRM / CANCEL (owner only, fail closed).

APPROVE never trusts the old suggested price: it re-quotes the contract
from UW. Within tolerance -> trade locked at the current Ask. Otherwise the
proposal moves to NEEDS_RECONFIRM and shows Suggested vs Current prices.

Every state change is a single conditional UPDATE, so a repeated or
concurrent button press can succeed only once.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from . import queries as q
from .entry import within_tolerance
from .models import ProposalStatus, TradeStatus
from .telegram_ui import (
    CB_APPROVE,
    CB_CANCEL,
    CB_RECONFIRM,
    CB_REJECT,
    contract_label,
    decision_footer,
    parse_callback,
    reconfirm_keyboard,
    reconfirm_text,
    usd,
)
from .timeutil import utcnow
from .uw_client import UWError

if TYPE_CHECKING:
    from .runtime import AutomationRuntime

logger = logging.getLogger("quiet-alpha.approval")


async def _message_html(query) -> Optional[str]:
    message = getattr(query, "message", None)
    return getattr(message, "text_html", None) if message is not None else None


class ApprovalHandler:
    def __init__(self, runtime: "AutomationRuntime") -> None:
        self.rt = runtime

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        parsed = parse_callback(query.data)
        if parsed is None:
            await query.answer()
            return

        user = query.from_user
        owner = self.rt.settings.owner_user_id
        if owner is None or user is None or user.id != owner:
            logger.warning(
                "Unauthorized callback from user %s",
                getattr(user, "id", None),
            )
            await query.answer("⛔ غير مصرح", show_alert=True)
            return

        if not self.rt.ready or not self.rt.settings.flags.proposals:
            await query.answer("الأتمتة غير متاحة حاليًا", show_alert=True)
            return

        action, proposal_id = parsed
        try:
            if action == CB_APPROVE:
                await self._approve(query, proposal_id, ProposalStatus.PENDING_APPROVAL, user.id)
            elif action == CB_RECONFIRM:
                await self._approve(query, proposal_id, ProposalStatus.NEEDS_RECONFIRM, user.id)
            elif action == CB_REJECT:
                await self._decline(query, proposal_id, ProposalStatus.PENDING_APPROVAL,
                                    ProposalStatus.REJECTED, user.id, "❌ REJECTED")
            elif action == CB_CANCEL:
                await self._decline(query, proposal_id, ProposalStatus.NEEDS_RECONFIRM,
                                    ProposalStatus.CANCELLED, user.id, "✖️ CANCELLED")
        except Exception:
            logger.exception("Callback %s for proposal #%s failed", action, proposal_id)
            try:
                await query.answer("حدث خطأ — راجعي السجل", show_alert=True)
            except Exception:
                pass

    # -----------------------------------------------------
    async def _decline(self, query, proposal_id, expected, new_status, user_id, footer) -> None:
        db = self.rt.db
        row = await db.fetchrow(
            q.DECIDE_PROPOSAL, proposal_id, expected.value, new_status.value, user_id,
            new_status.value.lower(),
        )
        if row is None:
            await self._answer_status(query, proposal_id)
            return
        await query.answer(footer)
        html = await _message_html(query)
        if html and query.message is not None:
            await self.rt.publisher.edit(
                query.message.message_id, decision_footer(html, f"<b>{footer}</b>")
            )
        logger.info("Proposal #%s %s", proposal_id, new_status.value)

    async def _answer_status(self, query, proposal_id: int) -> None:
        current = await self.rt.db.fetchrow(q.GET_PROPOSAL, proposal_id)
        status = current["status"] if current else "NOT_FOUND"
        await query.answer(f"الحالة الحالية: {status}", show_alert=True)
        if (
            current is not None
            and status in {"EXPIRED", "APPROVED", "REJECTED", "CANCELLED"}
            and query.message is not None
        ):
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

    async def _approve(self, query, proposal_id: int, expected: ProposalStatus,
                       user_id: int) -> None:
        rt = self.rt
        s = rt.settings
        proposal = await rt.db.fetchrow(q.BEGIN_APPROVAL, proposal_id, expected.value)
        if proposal is None:
            await self._answer_status(query, proposal_id)
            return
        await query.answer("⏳ إعادة طلب السعر من UW…")

        reference = (
            Decimal(proposal["reconfirm_price"])
            if expected is ProposalStatus.NEEDS_RECONFIRM and proposal["reconfirm_price"] is not None
            else Decimal(proposal["suggested_entry"])
        )
        symbol = proposal["option_symbol"]

        quote = None
        if rt.uw is not None:
            try:
                quotes, _ = await rt.uw.quotes_for([symbol])
                quote = quotes.get(symbol)
            except UWError as error:
                logger.warning("Approval re-quote failed (%s)", error.kind)

        if quote is None or quote.ask is None or quote.bid is None or quote.ask <= 0:
            await rt.db.execute(q.REVERT_APPROVAL, proposal_id)
            await rt.publisher.text(
                f"⚠️ تعذر جلب السعر الحالي لـ <code>{symbol}</code> من UW.\n"
                "الاقتراح ما زال مفتوحًا — حاولي الضغط مرة أخرى."
            )
            return

        current = quote.ask  # buy-side price used for entry
        if s.flags.flow_selection and current > s.selection.premium_max:
            replacement_id = await rt.pipeline.propose_alternative(proposal)
            if replacement_id is not None:
                if query.message is not None:
                    await query.edit_message_reply_markup(reply_markup=None)
                await rt.publisher.text("تجاوز العقد نطاق الطرح؛ نبحث عن بديل قوي بنفس الاتجاه، ويتطلب موافقة جديدة.")
                await rt._schedule_signal(replacement_id)
            else:
                await rt.db.execute(q.REVERT_APPROVAL, proposal_id)
                await rt.publisher.text("لا يمكن اعتماد العقد فوق الحد الأعلى؛ لم يتأكد بديل مناسب وزخم مستمر.")
            return
        if s.flags.flow_selection:
            from .models import Direction
            from .selection import select_with_flow
            signal = await rt.db.fetchrow(q.GET_SIGNAL, proposal["signal_db_id"])
            snapshot = await rt.pipeline._confirmed_flow(signal)
            selection = None if snapshot is None else select_with_flow(
                [quote], Direction(proposal["direction"]), proposal["expiry"],
                s.uw.contract_root, s.selection, utcnow(), snapshot,
            )
            if selection is None or selection.selected is None:
                await rt.db.execute(q.REVERT_APPROVAL, proposal_id)
                await rt.publisher.text("لم تعد شروط الاتجاه والتدفقات وجودة السعر مكتملة؛ لم تُعتمد الصفقة.")
                return
        in_range = s.selection.premium_min <= current <= s.selection.premium_max
        tolerance_ok = within_tolerance(
            reference, current, s.approval.tolerance_abs, s.approval.tolerance_pct
        )

        if tolerance_ok and (in_range or not s.approval.require_in_range):
            await self._lock_trade(query, proposal, current, quote, user_id)
            return

        reasons = []
        if not tolerance_ok:
            reasons.append("السعر تحرك خارج Entry Tolerance")
        if s.approval.require_in_range and not in_range:
            reasons.append("السعر خارج نطاق Premium المعتمد")
        expires_at = (
            utcnow() + timedelta(seconds=s.approval.proposal_expiry_seconds)
            if s.approval.proposal_expiry_seconds
            else None
        )
        updated = await rt.db.fetchrow(
            q.SET_NEEDS_RECONFIRM, proposal_id, current, quote.as_record(), expires_at
        )
        if updated is None:
            return
        html = await _message_html(query)
        if html and query.message is not None:
            await rt.publisher.edit(
                query.message.message_id,
                decision_footer(html, "<b>🔁 السعر تغيّر — مطلوب RECONFIRM</b>"),
            )
        label = contract_label(Decimal(proposal["strike"]), proposal["direction"])
        message_id = await rt.publisher.text(
            reconfirm_text(
                proposal_id,
                label,
                Decimal(proposal["suggested_entry"]),
                current,
                quote.bid,
                s.tracking.success_premium_gain,
                " · ".join(reasons),
            ),
            reply_markup=reconfirm_keyboard(proposal_id),
        )
        if message_id is not None:
            await rt.db.execute(q.SET_RECONFIRM_MESSAGE, proposal_id, message_id)
        logger.info("Proposal #%s needs reconfirm at %s", proposal_id, current)

    async def _lock_trade(self, query, proposal, approved_entry: Decimal, quote,
                          user_id: int) -> None:
        rt = self.rt
        signal = await rt.db.fetchrow(q.GET_SIGNAL, proposal["signal_db_id"])
        now = utcnow()
        trade = None
        limit_hit = False
        assert rt.db.pool is not None
        async with rt.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(q.LOCK_TRADE_DIRECTION, proposal["direction"])
                active_count = await conn.fetchval(
                    q.COUNT_TRACKING_DIRECTION, proposal["direction"]
                )
                if active_count >= 6:
                    limit_hit = True
                else:
                    finalized = await conn.fetchval(
                        q.FINALIZE_APPROVAL, proposal["id"], user_id,
                        f"approved at {approved_entry}",
                    )
                    if finalized is not None:
                        trade = await conn.fetchrow(
                            q.INSERT_TRADE,
                            proposal["id"],
                            proposal["signal_db_id"],
                            signal["signal_id"],
                            proposal["option_symbol"],
                            proposal["direction"],
                            proposal["strike"],
                            proposal["expiry"],
                            proposal["suggested_entry"],
                            approved_entry,
                            quote.as_record(),
                            proposal["classification"],
                            proposal["uw_watch_id"],
                            signal["signal_time"],
                            now,
                            quote.bid,
                            quote.ask,
                            quote.last,
                        )
        if limit_hit:
            await rt.db.execute(q.REVERT_APPROVAL, proposal["id"])
            await rt.publisher.text(
                f"⚠️ يوجد بالفعل 6 صفقات {proposal['direction']} مفتوحة.\n"
                "لم تُغلق أي صفقة، والاقتراح بقي مفتوحًا للمراجعة."
            )
            return
        if trade is None:
            return

        footer = (
            f"<b>✅ APPROVED — Entry {usd(approved_entry)} · Trade #{trade['id']}</b>\n"
            f"<i>Suggested {usd(Decimal(proposal['suggested_entry']))} · "
            f"Success {usd(approved_entry + rt.settings.tracking.success_premium_gain)}</i>"
        )
        html = await _message_html(query)
        if html and query.message is not None:
            await rt.publisher.edit(query.message.message_id, decision_footer(html, footer))
        logger.info(
            "Trade #%s LOCKED %s entry %s (%s)",
            trade["id"], trade["option_symbol"], approved_entry, TradeStatus.TRACKING.value,
        )
        # Entry card + tracking message now (the leader tracker also retries).
        await rt.tracker.ensure_started(trade)

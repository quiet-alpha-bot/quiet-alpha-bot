"""Trade tracker: same locked option_symbol, polled from UW REST.

Observed Bid = latest nbbo_bid returned by UW at poll time (UW Basic).

Per poll, for every TRACKING trade:
  * store a price observation
  * update current / highest / lowest (bid), highest mid, highest last
  * DETECT: every +10% level crossed (and SUCCESS = approved_entry + $1.00)
    is persisted first, with the Observed Bid of the detecting poll
  * PUBLISH: every detected-but-unsent event in threshold order, e.g.
    +10%, +20%, SUCCESS card, +30% — one ledger key each, sent once,
    never repeated after a drop; tracking continues after SUCCESS
  * edit the single LIVE TRACKING message

Restart-safe: state is restored from PostgreSQL. SENT events are not
republished automatically. Ambiguous interrupted deliveries are moved to
NEEDS_RECONCILIATION rather than blindly resent; exactly-once Telegram
delivery cannot be guaranteed across the send/DB crash window.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from . import queries as q
from .cards import CardRenderers, Publisher
from .config import Settings
from .db import Database
from .milestones import (
    TrackState,
    apply_observation,
    is_success,
    milestone_level,
    new_milestones,
    pending_events,
)
from .occ import strike_text
from .telegram_ui import contract_label, milestone_notification, tracking_text
from .timeutil import et_date, et_datetime, utcnow, within_session
from .uw_client import UWClient, UWError

logger = logging.getLogger("quiet-alpha.tracker")

# Milestone / SUCCESS events are retried until Telegram confirms delivery.
# A FAILED event was definitely not delivered, so retrying cannot duplicate.
ORDERED_EVENT_MAX_ATTEMPTS = 1_000_000


def label_for(trade) -> str:
    return contract_label(Decimal(trade["strike"]), trade["direction"])


class Tracker:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        uw: Optional[UWClient],
        publisher: Publisher,
        renderers: CardRenderers,
    ) -> None:
        self.s = settings
        self.db = db
        self.uw = uw
        self.publisher = publisher
        self.renderers = renderers
        self.last_tick_at: Optional[datetime] = None
        self.last_error: Optional[str] = None

    # -----------------------------------------------------
    # Lifecycle helpers (also called right after approval)
    # -----------------------------------------------------
    async def ensure_started(self, trade) -> None:
        """Entry card + live tracking message, each exactly once."""
        trade_id = trade["id"]
        await self.publisher.photo_once(
            "trade",
            trade_id,
            "ENTRY_CARD",
            lambda: self.renderers.signal_card(
                trade["direction"],
                Decimal(trade["strike"]),
                Decimal(trade["approved_entry"]),
            ),
        )
        if not self.s.flags.tracking or trade["tracking_message_id"] is not None:
            return
        message_id = await self.publisher.text_once(
            "trade", trade_id, "TRACKING_MESSAGE", self.render(trade)
        )
        if message_id is None:
            # Sent by a previous run but not saved? Recover the id.
            status = await self.db.fetchrow(
                q.EVENT_STATUS, "trade", trade_id, "TRACKING_MESSAGE", 0
            )
            if status and status["status"] == "SENT":
                message_id = status["message_id"]
        if message_id is not None:
            await self.db.execute(q.SET_TRACKING_MESSAGE, trade_id, message_id)

    def render(self, trade, closed_reason: Optional[str] = None,
               stale_note: Optional[str] = None) -> str:
        entry = Decimal(trade["approved_entry"])
        return tracking_text(
            label=label_for(trade),
            entry=entry,
            current_bid=trade["current_bid"],
            highest_bid=trade["highest_bid"],
            highest_milestone=trade["highest_milestone_sent"],
            step_pct=self.s.tracking.milestone_step_pct,
            success_detected=trade["success_detected_at"] is not None,
            success_gain=self.s.tracking.success_premium_gain,
            classification=trade["classification"],
            updated_at=trade["last_poll_at"] or utcnow(),
            tz_name=self.s.market.display_timezone,
            closed_reason=closed_reason,
            stale_note=stale_note,
        )

    async def refresh_message(self, trade_id: int, closed_reason: Optional[str] = None,
                              stale_note: Optional[str] = None) -> None:
        trade = await self.db.fetchrow(q.GET_TRADE, trade_id)
        if trade is None or trade["tracking_message_id"] is None:
            return
        ok = await self.publisher.edit(
            trade["tracking_message_id"], self.render(trade, closed_reason, stale_note)
        )
        if ok:
            await self.db.execute(q.TOUCH_TRACKING_MESSAGE, trade_id)

    async def close(self, trade_id: int, reason: str) -> Optional[dict]:
        closed = await self.db.fetchrow(q.CLOSE_TRADE, trade_id, reason)
        if closed is None:
            return None
        logger.info("Trade #%s closed (%s)", trade_id, reason)
        await self.refresh_message(trade_id, closed_reason=reason)
        if (
            self.s.flags.highest_card
            and self.s.tracking.highest_card_trigger == "on_close"
        ):
            await self.send_highest_card(closed)
        return closed

    async def send_highest_card(self, trade) -> Optional[int]:
        """Existing Highest Price card (same renderer as /max). Once per trade."""
        entry = Decimal(trade["approved_entry"])
        highest = trade["highest_bid"]
        if highest is None or Decimal(highest) <= entry:
            return None  # the existing card requires highest > entry
        return await self.publisher.photo_once(
            "trade",
            trade["id"],
            "HIGHEST_CARD",
            lambda: self.renderers.highest_card(entry, Decimal(highest)),
            payload={"highest_bid": str(highest), "time": str(trade["highest_bid_time"])},
        )

    async def _event_done(self, trade_id: int, event_type: str, level: int) -> bool:
        """Only a SENT event counts as published.

        FAILED   -> not delivered; stays pending and is retried next poll
                    (also after a restart). Later events wait.
        UNCERTAIN-> delivery unknown; never assumed SENT, never resent.
                    Later events of this trade stay blocked.
        PENDING  -> in progress (or interrupted); later events wait.
        NEEDS_RECONCILIATION -> stale PENDING; waits for /qa_reconcile.
        """
        row = await self.db.fetchrow(q.EVENT_STATUS, "trade", trade_id, event_type, level)
        if row is None:
            return False
        if row["status"] == "SENT":
            return True
        if row["status"] == "UNCERTAIN":
            logger.warning("Trade #%s %s %s is UNCERTAIN; later events are blocked",
                           trade_id, event_type, level)
        return False

    async def flush_events(self, trade_id: int) -> bool:
        """Publish detected-but-unsent events in threshold order.

        Each event has its own ledger key: (trade, MILESTONE, n) and
        (trade, SUCCESS_CARD). If one cannot be delivered yet, later events
        wait, so the order is preserved after retries and restarts.
        """
        trade = await self.db.fetchrow(q.GET_TRADE, trade_id)
        if trade is None:
            return False
        entry = Decimal(trade["approved_entry"])
        detections = {
            int(level): Decimal(str(bid))
            for level, bid in (trade["milestone_detections"] or {}).items()
        }
        success_pending = bool(
            self.s.flags.success_card
            and trade["success_detected_at"] is not None
            and not trade["success_sent"]
        )
        events = pending_events(
            entry,
            self.s.tracking.milestone_step_pct,
            trade["highest_milestone_sent"],
            trade["highest_milestone_reached"] if self.s.flags.milestones else 0,
            detections,
            success_pending,
            trade["success_bid"],
            self.s.tracking.success_premium_gain,
        )
        label = label_for(trade)
        progressed = False
        for event in events:
            bid = event.bid if event.bid is not None else trade["current_bid"]
            if event.kind == "MILESTONE":
                await self.publisher.text_once(
                    "trade", trade_id, "MILESTONE",
                    milestone_notification(label, event.level, bid),
                    level=event.level,
                    payload={"observed_bid": str(bid), "threshold": str(event.threshold)},
                    max_attempts=ORDERED_EVENT_MAX_ATTEMPTS,
                )
                if not await self._event_done(trade_id, "MILESTONE", event.level):
                    break
                await self.db.execute(q.SET_TRADE_MILESTONE, trade_id, event.level)
            else:
                await self.publisher.photo_once(
                    "trade", trade_id, "SUCCESS_CARD",
                    lambda: self.renderers.success_card(entry, Decimal(bid)),
                    payload={"observed_bid": str(bid), "threshold": str(event.threshold)},
                    max_attempts=ORDERED_EVENT_MAX_ATTEMPTS,
                )
                if not await self._event_done(trade_id, "SUCCESS_CARD", 0):
                    break
                await self.db.execute(q.MARK_SUCCESS_SENT, trade_id)
            progressed = True
        return progressed

    # -----------------------------------------------------
    # Loop
    # -----------------------------------------------------
    async def run(self) -> None:
        logger.info("Tracker started")
        while True:
            try:
                await self.tick()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("Tracker tick failed")
            await asyncio.sleep(self.s.tracking.poll_seconds)

    async def recover_stale_pending(self) -> list:
        """PENDING older than EVENT_PENDING_STALE_SECONDS -> NEEDS_RECONCILIATION.

        The process may have died before or after Telegram accepted the
        message; the Bot API cannot tell us which. So the event is neither
        resent nor assumed SENT: the owner is alerted once and decides with
        /qa_reconcile. Until then, later events of that trade stay blocked.
        """
        rows = await self.db.fetch(
            q.MARK_STALE_PENDING, float(self.s.tracking.pending_stale_seconds)
        )
        for row in rows:
            level = f" {row['level']}" if row["level"] else ""
            logger.error("Event #%s %s/%s %s%s stale PENDING -> NEEDS_RECONCILIATION",
                         row["id"], row["entity_type"], row["entity_id"],
                         row["event_type"], level)
            await self.publisher.text(
                "⚠️ <b>QUIET ALPHA — Reconciliation</b>\n"
                f"Event #{row['id']}: {row['event_type']}{level} "
                f"({row['entity_type']} #{row['entity_id']})\n"
                "انقطع البوت أثناء الإرسال، ولا يمكن معرفة هل وصلت الرسالة.\n"
                "الأحداث التالية لهذه الصفقة متوقفة حتى تقرري:\n"
                f"• ظهرت الرسالة في القناة: <code>/qa_reconcile {row['id']} sent</code>\n"
                f"• لم تظهر — أعيدي الإرسال: <code>/qa_reconcile {row['id']} resend</code>"
            )
        return rows

    async def reconcile_event(self, event_id: int, action: str):
        """Owner decision for a NEEDS_RECONCILIATION event.

        sent   -> SENT   (owner saw the message; the queue continues)
        resend -> FAILED (the normal FAILED retry path sends it once)
        """
        status = {"sent": "SENT", "resend": "FAILED"}.get(action)
        if status is None:
            raise ValueError("action must be 'sent' or 'resend'")
        return await self.db.fetchrow(q.RECONCILE_EVENT, event_id, status)

    async def tick(self) -> None:
        await self.recover_stale_pending()
        trades = await self.db.fetch(q.TRACKING_TRADES)
        self.last_tick_at = utcnow()
        if not trades:
            return

        now = utcnow()
        open_trades = []
        for trade in trades:
            await self.ensure_started(trade)
            # Complete anything detected but not yet published (e.g. after a
            # restart), before any new observation and before closing.
            try:
                if await self.flush_events(trade["id"]):
                    await self.refresh_message(trade["id"])
            except Exception:
                logger.exception("Tracker: flush for trade #%s failed", trade["id"])
            stop_at = et_datetime(trade["expiry"], self.s.tracking.stop_time_et)
            if now >= stop_at or et_date(now) > trade["expiry"]:
                await self.close(trade["id"], "SESSION_END")
                continue
            open_trades.append(trade)

        if not open_trades or self.uw is None:
            return
        if not within_session(now, self.s.market.open_et, self.s.market.close_et):
            return

        symbols = sorted({t["option_symbol"] for t in open_trades})
        try:
            quotes, response = await self.uw.quotes_for(symbols)
        except UWError as error:
            for trade in open_trades:
                await self.db.execute(q.SET_TRADE_ERROR, trade["id"], f"UW {error.kind}", now)
            logger.warning("Tracker: UW quote poll failed (%s)", error.kind)
            return

        latency = response.latency_ms if response else None
        for trade in open_trades:
            quote = quotes.get(trade["option_symbol"])
            try:
                await self._process(trade, quote, latency)
            except Exception:
                logger.exception("Tracker: trade #%s update failed", trade["id"])

    async def _process(self, trade, quote, latency: Optional[int]) -> None:
        trade_id = trade["id"]
        now = utcnow()
        if quote is None:
            await self.db.execute(
                q.INSERT_PRICE_OBSERVATION, trade_id, now, None, None, None, None,
                None, None, latency, None, "NOT_RETURNED",
            )
            await self.db.execute(q.SET_TRADE_ERROR, trade_id, "QUOTE_NOT_RETURNED", now)
            return

        changed = (quote.bid != trade["current_bid"]) or (quote.ask != trade["current_ask"])
        state = TrackState(
            current_bid=trade["current_bid"],
            current_ask=trade["current_ask"],
            current_last=trade["current_last"],
            highest_bid=trade["highest_bid"],
            highest_bid_time=trade["highest_bid_time"],
            highest_mid=trade["highest_mid"],
            highest_last=trade["highest_last"],
            lowest_bid=trade["lowest_bid"],
            lowest_bid_time=trade["lowest_bid_time"],
        )
        state, new_high = apply_observation(state, quote.bid, quote.ask, quote.last, now)

        await self.db.execute(
            q.INSERT_PRICE_OBSERVATION, trade_id, now, quote.bid, quote.ask, quote.last,
            quote.volume, quote.open_interest, quote.last_tape_time, latency, changed, None,
        )
        await self.db.execute(
            q.UPDATE_TRADE_QUOTE, trade_id,
            state.current_bid, state.current_ask, state.current_last,
            state.highest_bid, state.highest_bid_time, state.highest_mid,
            state.highest_last, state.lowest_bid, state.lowest_bid_time,
            now, quote.last_tape_time,
        )

        entry = Decimal(trade["approved_entry"])
        significant = False

        # ---- 1) DETECT (persist first, publish second) --------------
        if self.s.flags.milestones and quote.bid is not None:
            step = self.s.tracking.milestone_step_pct
            reached = milestone_level(entry, quote.bid, step)
            newly = new_milestones(trade["highest_milestone_reached"], reached, step)
            if newly:
                await self.db.execute(
                    q.RECORD_MILESTONE_DETECTIONS,
                    trade_id,
                    reached,
                    {str(level): str(quote.bid) for level in newly},
                )
                significant = True

        gain = self.s.tracking.success_premium_gain
        # SUCCESS is detected only while SUCCESS_CARD_ENABLED is on, from the
        # current observation only (no backfill from earlier prices).
        if (
            self.s.flags.success_card
            and trade["success_detected_at"] is None
            and is_success(entry, quote.bid, gain)
        ):
            if await self.db.fetchval(q.MARK_SUCCESS_DETECTED, trade_id, now, quote.bid):
                significant = True
                logger.info("Trade #%s SUCCESS at Observed Bid %s", trade_id, quote.bid)

        # ---- 2) PUBLISH every pending event in threshold order -------
        if await self.flush_events(trade_id):
            significant = True

        # Close only after at least one profit milestone was reached and
        # the executable bid later falls below the approved entry. Closing
        # here lets HIGHEST_CARD_TRIGGER=on_close publish the trade's peak.
        if (
            quote.bid is not None
            and int(trade["highest_milestone_reached"] or 0) > 0
            and quote.bid < entry
        ):
            await self.close(trade_id, "FELL_BELOW_ENTRY")
            return

        # ---- live tracking message ----------------------------------
        if not self.s.flags.tracking:
            return
        refresh = self.s.tracking.message_refresh_seconds
        edited_at = trade["tracking_message_edited_at"]
        due = (
            refresh > 0
            and changed
            and (edited_at is None or (now - edited_at).total_seconds() >= refresh)
        )
        if significant or due or (new_high and refresh > 0 and due):
            await self.refresh_message(trade_id)


__all__ = ["Tracker", "strike_text"]


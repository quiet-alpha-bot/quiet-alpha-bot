"""Indicator signal pipeline:

signal -> classification (INDICATOR_ONLY / BOTH_CONFIRMED / CONFLICT)
       -> SPXW contract selection (UW)
       -> entry selection (ENTER / WAIT / SKIP)
       -> private trade proposal (APPROVE / REJECT)

A UW watch is never required: without one the signal is INDICATOR_ONLY
and continues normally.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from . import queries as q
from .cards import Publisher
from .config import Settings, strategy_snapshot
from .db import Database
from .entry import EntryObservation, decide_entry
from .matching import MatchResult, WatchRef, classify_signal
from .models import (
    Classification,
    Direction,
    EntryDecision,
    SignalStatus,
    WatchStatus,
)
from .selection import select_contract, selection_reasons
from .telegram_ui import compact_usd, proposal_keyboard, proposal_text, signal_status_text
from .timeutil import et_date, utcnow, within_session
from .uw_client import UWClient, UWError

logger = logging.getLogger("quiet-alpha.pipeline")


def _seconds(value: Optional[float]) -> Optional[Decimal]:
    """NUMERIC parameters are passed as Decimal (asyncpg-safe)."""
    return None if value is None else Decimal(str(round(value, 3)))


class SignalPipeline:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        uw: Optional[UWClient],
        publisher: Publisher,
    ) -> None:
        self.s = settings
        self.db = db
        self.uw = uw
        self.publisher = publisher

    # -----------------------------------------------------
    async def process(self, signal_db_id: int) -> None:
        row = await self.db.fetchrow(q.CLAIM_SIGNAL, signal_db_id)
        if row is None:
            return  # already claimed elsewhere
        try:
            await self._process(row)
        except Exception:
            logger.exception("Signal #%s failed", signal_db_id)
            await self._finish(row, SignalStatus.DATA_ERROR, "UNEXPECTED_ERROR")

    async def _finish(self, row, status: SignalStatus, reason: Optional[str]) -> None:
        await self.db.execute(q.FINISH_SIGNAL, row["id"], status.value, reason)
        if self.s.notify_signal_status and status not in (
            SignalStatus.PROPOSED,
            SignalStatus.RECORDED,
        ):
            await self.publisher.text(
                signal_status_text(row["direction"], status.value, reason, row["signal_id"])
            )

    # -----------------------------------------------------
    async def classify(self, row) -> tuple[Optional[MatchResult], Optional[bool], dict]:
        if not self.s.flags.matching:
            return None, None, {}
        signal_time = row["signal_time"]
        direction = Direction(row["direction"])
        window = timedelta(minutes=self.s.match.window_minutes)

        watch_rows = await self.db.fetch(
            q.WATCHES_IN_WINDOW, signal_time, signal_time - window
        )
        watches = [
            WatchRef(r["id"], Direction(r["direction"]), r["watch_time"])
            for r in watch_rows
        ]
        result = classify_signal(
            direction, signal_time, watches, window, self.s.match.mixed_policy
        )

        # Was the watch engine actually observing around the signal time?
        last_ok = await self.db.fetchval(q.LAST_GOOD_OBSERVATION)
        coverage = timedelta(seconds=max(3 * self.s.watch.poll_seconds, 180))
        watch_active = bool(
            self.s.flags.uw_watch
            and last_ok is not None
            and abs((signal_time - last_ok).total_seconds()) <= coverage.total_seconds()
        )

        await self.db.fetchval(
            q.INSERT_MATCH,
            result.classification.value,
            row["id"],
            result.primary_watch.id if result.primary_watch else None,
            direction.value,
            result.primary_watch.direction.value if result.primary_watch else None,
            signal_time,
            result.primary_watch.watch_time if result.primary_watch else None,
            _seconds(result.time_difference_seconds(signal_time)),
            result.related_ids,
            watch_active,
            self.s.match.rules_version,
            self.s.match.window_minutes,
            utcnow(),
        )
        await self.db.execute(
            q.SET_SIGNAL_CLASSIFICATION, row["id"], result.classification.value
        )
        if result.same_direction:
            await self.db.execute(
                q.MARK_WATCHES,
                [w.id for w in result.same_direction],
                WatchStatus.MATCHED.value,
            )
        if result.opposite_direction:
            await self.db.execute(
                q.MARK_WATCHES,
                [w.id for w in result.opposite_direction],
                WatchStatus.CONFLICT.value,
            )
        logger.info(
            "Signal #%s classified %s", row["id"], result.classification.value
        )
        return result, watch_active, {r["id"]: r for r in watch_rows}

    @staticmethod
    def _uw_context(result: Optional[MatchResult], signal_time, watch_rows: dict) -> Optional[str]:
        if result is None or result.primary_watch is None:
            return None
        watch = watch_rows.get(result.primary_watch.id)
        minutes = int((signal_time - result.primary_watch.watch_time).total_seconds() // 60)
        parts = [f"{result.primary_watch.direction.value} watch قبل {minutes}m"]
        if watch is not None:
            if watch["net_premium"] is not None:
                parts.append(f"Net {compact_usd(watch['net_premium'])}")
            if watch["strength"] is not None:
                parts.append(f"Strength {Decimal(watch['strength']).quantize(Decimal('0.01'))}")
        return " · ".join(parts)

    # -----------------------------------------------------
    async def _process(self, row) -> None:
        direction = Direction(row["direction"])
        signal_time = row["signal_time"]

        result, watch_active, watch_rows = await self.classify(row)

        if not self.s.flags.selection:
            await self._finish(row, SignalStatus.RECORDED, "SELECTION_DISABLED")
            return
        if self.uw is None:
            await self._finish(row, SignalStatus.DATA_ERROR, "UW_NOT_CONFIGURED")
            return

        now = utcnow()
        if not within_session(now, self.s.market.open_et, self.s.market.close_et):
            await self._finish(row, SignalStatus.SKIPPED, "OUTSIDE_SESSION")
            return

        run_id = await self.db.fetchval(q.INSERT_RUN, row["id"], strategy_snapshot(self.s))
        expiry = et_date(now)

        try:
            ticker, quotes, meta = await self.uw.chain(direction, expiry)
        except UWError as error:
            await self._finish_run(run_id, "DATA_ERROR", f"UW {error.kind}", None, expiry, None, [], None, {})
            await self._finish(row, SignalStatus.DATA_ERROR, f"UW_{error.kind.upper()}")
            return

        spot = None
        if self.s.selection.weight_distance > 0:
            spot = await self.uw.spot(self.s.uw.spot_ticker)

        selection = select_contract(
            quotes,
            direction,
            expiry,
            self.s.uw.contract_root,
            self.s.selection,
            utcnow(),
            spot,
        )
        await self._store_candidates(run_id, selection.candidates)

        if selection.selected is None:
            reason = "NO_ELIGIBLE_CONTRACT" if quotes else "EMPTY_CHAIN"
            await self._finish_run(run_id, "NO_CONTRACT", reason, ticker, expiry, spot,
                                   selection.candidates, None, meta)
            await self._finish(row, SignalStatus.NO_CONTRACT, reason)
            return

        chosen = selection.selected.quote
        entry_result, latest = await self._entry_loop(run_id, chosen, signal_time)
        if entry_result.decision is not EntryDecision.ENTER:
            await self._finish_run(run_id, "SKIPPED", entry_result.reason, ticker, expiry,
                                   spot, selection.candidates, chosen.symbol, meta)
            await self._finish(row, SignalStatus.SKIPPED, entry_result.reason)
            return

        suggested = entry_result.price
        await self._finish_run(run_id, "SELECTED", entry_result.reason, ticker, expiry,
                               spot, selection.candidates, chosen.symbol, meta)

        if not self.s.flags.proposals:
            await self._finish(
                row,
                SignalStatus.SELECTED,
                f"{chosen.symbol} @ ${suggested} (proposals disabled)",
            )
            return

        reasons = selection_reasons(selection)
        expires_at = (
            utcnow() + timedelta(seconds=self.s.approval.proposal_expiry_seconds)
            if self.s.approval.proposal_expiry_seconds
            else None
        )
        details = {
            "reasons": reasons,
            "entry_reason": entry_result.reason,
            "uw_context": self._uw_context(result, signal_time, watch_rows),
            "watch_engine_active": watch_active,
            "event_type": row["event_type"],
            "chain_ticker": ticker,
        }
        proposal = await self.db.fetchrow(
            q.INSERT_PROPOSAL,
            row["id"],
            run_id,
            direction.value,
            chosen.symbol,
            chosen.occ.strike,
            chosen.occ.expiry,
            suggested,
            latest.bid,
            latest.ask,
            latest.last,
            result.classification.value if result else None,
            result.primary_watch.id if (result and result.primary_watch) else None,
            details,
            expires_at,
        )
        if proposal is None:
            return  # proposal already exists for this signal

        text = proposal_text(
            proposal_id=proposal["id"],
            direction=direction.value,
            symbol=chosen.symbol,
            strike=chosen.occ.strike,
            expiry_text=f"TODAY ({chosen.occ.expiry.isoformat()})",
            suggested_entry=suggested,
            bid=latest.bid,
            ask=latest.ask,
            last=latest.last,
            success_gain=self.s.tracking.success_premium_gain,
            classification=result.classification.value if result else None,
            watch_engine_active=watch_active,
            uw_context=details["uw_context"],
            reasons=reasons,
            signal_time=signal_time,
            event_type=row["event_type"],
            expires_at=expires_at,
            tz_name=self.s.market.display_timezone,
        )
        message_id = await self.publisher.text_once(
            "proposal",
            proposal["id"],
            "PROPOSAL",
            text,
            reply_markup=proposal_keyboard(proposal["id"]),
        )
        if message_id is not None:
            await self.db.execute(q.SET_PROPOSAL_MESSAGE, proposal["id"], message_id)
        await self._finish(row, SignalStatus.PROPOSED, f"proposal #{proposal['id']}")

    # -----------------------------------------------------
    async def _entry_loop(self, run_id: int, chosen, signal_time):
        e = self.s.entry
        first_ask = chosen.ask
        window_start = utcnow()
        history = [EntryObservation(window_start, chosen.bid, chosen.ask)]
        latest_quote = chosen
        max_rounds = (e.window_seconds // max(1, e.poll_seconds)) + 3

        for _ in range(max_rounds):
            now = utcnow()
            result = decide_entry(
                history, first_ask, signal_time, window_start, now, e, self.s.selection
            )
            latest = history[-1]
            await self.db.execute(
                q.INSERT_ENTRY_DECISION,
                run_id,
                result.decision.value,
                result.reason,
                latest.bid,
                latest.ask,
                latest.spread_pct,
                Decimal(str(round((now - signal_time).total_seconds(), 3))),
                {"observations": len(history), "first_ask": str(first_ask)},
            )
            if result.decision is not EntryDecision.WAIT:
                return result, latest_quote
            await asyncio.sleep(e.poll_seconds)
            try:
                quotes, _ = await self.uw.quotes_for([chosen.symbol])
                quote = quotes.get(chosen.symbol)
            except UWError:
                quote = None
            if quote is not None:
                latest_quote = quote
                history.append(EntryObservation(utcnow(), quote.bid, quote.ask))
            else:
                history.append(EntryObservation(utcnow(), None, None))

        final = decide_entry(
            history, first_ask, signal_time, window_start,
            window_start + timedelta(seconds=e.window_seconds + 1), e, self.s.selection,
        )
        return final, latest_quote

    async def _store_candidates(self, run_id: int, candidates) -> None:
        rows = []
        for c in candidates:
            qt = c.quote
            rows.append(
                (
                    run_id,
                    qt.symbol,
                    qt.occ.strike,
                    qt.occ.expiry,
                    qt.occ.right,
                    qt.bid,
                    qt.ask,
                    qt.last,
                    qt.mid,
                    qt.spread,
                    qt.spread_pct,
                    qt.volume,
                    qt.open_interest,
                    qt.delta,
                    qt.last_tape_time,
                    c.passed,
                    c.reject_reasons,
                    c.score,
                    c.breakdown or None,
                    c.rank,
                )
            )
        await self.db.executemany(q.INSERT_CANDIDATE, rows)

    async def _finish_run(self, run_id, status, reason, ticker, expiry, spot,
                          candidates, selected, meta) -> None:
        await self.db.execute(
            q.FINISH_RUN,
            run_id,
            status,
            reason,
            ticker,
            expiry,
            spot,
            len(candidates),
            sum(1 for c in candidates if c.passed),
            selected,
            meta or {},
        )


__all__ = ["SignalPipeline", "Classification"]

"""Indicator signal pipeline:

signal -> classification (INDICATOR_ONLY / BOTH_CONFIRMED / CONFLICT)
       -> SPXW contract selection (UW)
       -> entry selection (ENTER / WAIT / SKIP)
       -> private trade proposal (APPROVE / REJECT)

With FLOW_SELECTION_ENABLED off, a UW watch is not required. With the
new mode on, current single-leg buying flow and a fresh, aligned indicator
are required for both original and alternative proposals.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, InvalidOperation
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
from .selection import select_contract, selection_reasons, select_with_flow
from .telegram_ui import compact_usd, proposal_keyboard, proposal_text, signal_status_text
from .timeutil import et_date, utcnow, within_session
from .uw_client import UWClient, UWError

logger = logging.getLogger("quiet-alpha.pipeline")
# Context-local so simultaneous CALL/PUT signals cannot overwrite diagnostics.
flow_rejection_reason = ContextVar("flow_rejection_reason", default="سبب غير محدد")


def reject_flow(reason):
    flow_rejection_reason.set(reason)
    logger.warning("Flow confirmation rejected: %s", reason)
    return None


def _seconds(value: Optional[float]) -> Optional[Decimal]:
    """NUMERIC parameters are passed as Decimal (asyncpg-safe)."""
    return None if value is None else Decimal(str(round(value, 3)))


def _percent(value) -> Optional[Decimal]:
    """Parse and clamp a percentage coming from an external payload."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return max(Decimal("0"), min(Decimal("100"), parsed))


def _payload_strength(row) -> Optional[Decimal]:
    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    return _percent(payload.get("strength"))


def _proposal_strengths(row, result, watch_rows: dict):
    """Return TV, UW and 50/50 composite strength for the proposal card."""
    tv_strength = _payload_strength(row)
    uw_strength = None
    uw_aligned = None

    if result is not None and result.primary_watch is not None:
        watch = watch_rows.get(result.primary_watch.id)
        if watch is not None and watch["strength"] is not None:
            raw = abs(Decimal(str(watch["strength"])))
            # UW watch strength is normally 0..1; tolerate an already-percent value.
            uw_strength = _percent(raw * 100 if raw <= 1 else raw)
            uw_aligned = result.primary_watch.direction.value == row["direction"]

    if tv_strength is not None and uw_strength is not None:
        uw_contribution = uw_strength if uw_aligned else Decimal("100") - uw_strength
        composite = (tv_strength + uw_contribution) / Decimal("2")
    elif tv_strength is not None:
        composite = tv_strength
    elif uw_strength is not None:
        composite = uw_strength if uw_aligned else Decimal("100") - uw_strength
    else:
        composite = None

    return tv_strength, uw_strength, _percent(composite), uw_aligned


def live_proposal_strengths(row, snapshot):
    """A descriptive agreement score, never a probability of winning."""
    tv = snapshot.indicator_strength
    if tv is None:
        tv = _payload_strength(row)
    uw = _percent(abs(snapshot.market_strength) * 100) if snapshot.market_strength is not None else None
    directional = snapshot.market_directional
    aligned = (directional is not None and
               ((row["direction"] == "CALL" and directional > 0) or
                (row["direction"] == "PUT" and directional < 0)))
    combined = (tv + (uw if aligned else 100-uw))/2 if tv is not None and uw is not None else None
    return tv, uw, _percent(combined), aligned


class SignalPipeline:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        uw: Optional[UWClient],
        publisher: Publisher,
        liquidity=None,
    ) -> None:
        self.s = settings
        self.db = db
        self.uw = uw
        self.publisher = publisher
        self.liquidity = liquidity
        self.autonomous = None

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
        if row.get("source") == "autonomous":
            result = MatchResult(Classification.UW_ONLY, None, (), ())
            await self.db.execute(q.SET_SIGNAL_CLASSIFICATION, row["id"], result.classification.value)
            return result, True, {}
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
        requires_flow = (row.get("source") == "autonomous" or self.s.flags.flow_selection
                         or bool(self._signal_payload(row).get("alternative_count")))
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

        blocked_strikes = await self._open_strikes(direction)
        excluded = set(self._signal_payload(row).get("excluded_symbols", []))
        quotes = [quote for quote in quotes if quote.symbol not in excluded]
        if requires_flow:
            snapshot = await self._confirmed_flow(row)
            if snapshot is None:
                reason = "MOMENTUM_OR_FLOW_UNCONFIRMED — " + flow_rejection_reason.get()
                await self._finish(row, SignalStatus.SKIPPED, reason)
                await self._finish_run(run_id, "SKIPPED", reason,
                                       ticker, expiry, spot, [], None, meta)
                return
            if row.get("source") == "autonomous":
                quotes = [qt for qt in quotes if qt.symbol in snapshot.contracts]
            selection = select_with_flow(quotes, direction, expiry, self.s.uw.contract_root,
                                         self.s.selection, utcnow(), snapshot, spot, excluded_strikes=blocked_strikes)
        else:
            selection = select_contract(quotes, direction, expiry, self.s.uw.contract_root,
                                        self.s.selection, utcnow(), spot, excluded_strikes=blocked_strikes)
        await self._store_candidates(run_id, selection.candidates)

        if selection.selected is None:
            reason = "NO_ELIGIBLE_CONTRACT" if quotes else "EMPTY_CHAIN"
            await self._finish_run(run_id, "NO_CONTRACT", reason, ticker, expiry, spot,
                                   selection.candidates, None, meta)
            await self._finish(row, SignalStatus.NO_CONTRACT, reason)
            return

        chosen = selection.selected.quote
        attempts = 0
        while True:
            entry_result, latest, pre_proposal_high = await self._entry_loop(
                run_id, chosen, signal_time
            )
            if entry_result.decision is EntryDecision.ENTER:
                break
            overpriced = latest is not None and latest.ask is not None and latest.ask > self.s.selection.premium_max
            if not (requires_flow and row.get("source") != "autonomous" and overpriced
                    and attempts < self.s.selection.alternative_attempts):
                await self._finish_run(run_id, "SKIPPED", entry_result.reason, ticker, expiry,
                                       spot, selection.candidates, chosen.symbol, meta)
                await self._finish(row, SignalStatus.SKIPPED, entry_result.reason)
                return
            attempts += 1
            excluded.add(chosen.symbol)
            snapshot = await self._confirmed_flow(row)
            if snapshot is None:
                await self._finish(row, SignalStatus.SKIPPED, "ALTERNATIVE_MOMENTUM_LOST")
                await self._finish_run(run_id, "SKIPPED", "ALTERNATIVE_MOMENTUM_LOST",
                                       ticker, expiry, spot, [], None, meta)
                return
            ticker, fresh_quotes, meta = await self.uw.chain(direction, expiry)
            blocked_strikes = await self._open_strikes(direction)
            selection = select_with_flow(
                [qt for qt in fresh_quotes if qt.symbol not in excluded], direction, expiry,
                self.s.uw.contract_root, self.s.selection, utcnow(), snapshot, spot,
                excluded_strikes=blocked_strikes,
            )
            await self._store_candidates(run_id, selection.candidates)
            if selection.selected is None:
                await self._finish(row, SignalStatus.NO_CONTRACT, "NO_STRONG_ALTERNATIVE")
                await self._finish_run(run_id, "NO_CONTRACT", "NO_STRONG_ALTERNATIVE",
                                       ticker, expiry, spot, selection.candidates, None, meta)
                return
            chosen = selection.selected.quote

        if chosen.occ.strike in await self._open_strikes(direction):
            await self._finish_run(run_id, "SKIPPED", "STRIKE_ALREADY_OPEN", ticker,
                                   expiry, spot, selection.candidates, chosen.symbol, meta)
            await self._finish(row, SignalStatus.SKIPPED, "STRIKE_ALREADY_OPEN")
            return

        if requires_flow:
            snapshot = await self._confirmed_flow(row)
            verification = None if snapshot is None else select_with_flow(
                [latest], direction, expiry, self.s.uw.contract_root,
                self.s.selection, utcnow(), snapshot, spot,
            )
            if (verification is None or verification.selected is None
                or not await self.quote_matches_trigger(row, latest)):
                await self._finish_run(run_id, "SKIPPED", "PRE_PROPOSAL_RECHECK_FAILED", ticker,
                                       expiry, spot, selection.candidates, chosen.symbol, meta)
                await self._finish(row, SignalStatus.SKIPPED, "PRE_PROPOSAL_RECHECK_FAILED")
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
        if requires_flow:
            reasons.insert(0, "الأقوى حسب صافي تدفقات الشراء الحديثة بين العقود المؤهلة")
            if attempts or self._signal_payload(row).get("alternative_count"):
                reasons.insert(0, "استرايك بديل بعد خروج سعر العقد السابق عن نطاق الطرح")
        if row.get("source") == "autonomous":
            reasons.insert(0, "عودة السعر والزخم والسيولة بعد إغلاق صفقة ناجحة" if row["event_type"] == "REENTRY"
                           else "رادار عقود اليوم؛ العقد المرشح اجتاز فحص السيولة والزخم الخاص به")
        live_confirmation = None
        if requires_flow:
            tv_strength, uw_strength, composite_strength, uw_aligned = live_proposal_strengths(row, snapshot)
            live_confirmation = {
                "autonomous": row.get("source") == "autonomous",
                "window_minutes": 5 if row.get("source") == "autonomous" else self.s.selection.flow_window_minutes,
                "direction": row["direction"],
                "directional": str(snapshot.market_directional),
                "start": snapshot.start.isoformat(), "end": snapshot.end.isoformat(),
                "watch_status": result.classification.value if result else None,
            }
        else:
            tv_strength, uw_strength, composite_strength, uw_aligned = _proposal_strengths(row, result, watch_rows)
        expires_at = (
            utcnow() + timedelta(seconds=self.s.approval.proposal_expiry_seconds)
            if self.s.approval.proposal_expiry_seconds
            else None
        )
        details = {
            "reasons": reasons,
            "requires_flow": requires_flow,
            "live_confirmation": live_confirmation,
            "entry_reason": entry_result.reason,
            "uw_context": self._uw_context(result, signal_time, watch_rows),
            "watch_engine_active": watch_active,
            "event_type": row["event_type"],
            "chain_ticker": ticker,
            "tv_strength": str(tv_strength) if tv_strength is not None else None,
            "uw_strength": str(uw_strength) if uw_strength is not None else None,
            "composite_strength": str(composite_strength) if composite_strength is not None else None,
            "uw_aligned": uw_aligned,
            "pre_proposal_high": (
                str(pre_proposal_high) if pre_proposal_high is not None else None
            ),
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

        channel = self._signal_payload(row).get("publication_chat_id") if self._signal_payload(row).get("alternative_count") else None
        if channel:
            from .channel_routing import PUBLIC_CHAT, PRIVATE_CHAT
            if channel not in (PUBLIC_CHAT, PRIVATE_CHAT):
                raise ValueError("Invalid inherited publication channel")
            await self.db.execute(q.SET_ALTERNATIVE_CHANNEL, proposal["id"], channel)

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
            tv_strength=tv_strength,
            uw_strength=uw_strength,
            composite_strength=composite_strength,
            uw_aligned=uw_aligned,
            pre_proposal_high=pre_proposal_high,
            live_confirmation=live_confirmation,
        )
        if channel:
            from .channel_routing import channel_name
            text += f"\nقناة النشر المحفوظة: <b>{channel_name(channel)}</b> — يلزم تأكيد البديل."
        message_id = await self.publisher.text_once(
            "proposal",
            proposal["id"],
            "PROPOSAL",
            text,
            reply_markup=proposal_keyboard(proposal["id"], channel),
        )
        if message_id is not None:
            await self.db.execute(q.SET_PROPOSAL_MESSAGE, proposal["id"], message_id)
        await self._finish(row, SignalStatus.PROPOSED, f"proposal #{proposal['id']}")

    @staticmethod
    def _signal_payload(row):
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload if isinstance(payload, dict) else {}

    async def _confirmed_flow(self, row):
        flow_rejection_reason.set("سبب غير محدد")
        if row.get("source") == "autonomous":
            if self.autonomous is None:
                return reject_flow("رصد الفرص المستقلة غير متاح")
            snapshot = await self.autonomous.validate(row)
            return snapshot if snapshot is not None else reject_flow("لم تتأكد عودة السعر والزخم والسيولة للعقد")
        if self.liquidity is None:
            return reject_flow("مصدر التدفقات غير متصل")
        now = utcnow()
        age = (now - row["signal_time"]).total_seconds()
        if age < 0:
            return reject_flow("وقت الإشارة في المستقبل؛ يلزم مراجعة التوقيت")
        if age > self.s.selection.momentum_max_age_seconds:
            return reject_flow(f"الإشارة قديمة: {int(age)} ثانية")
        latest = await self.db.fetchrow(q.LATEST_DIRECTION_SIGNAL,
            now - timedelta(seconds=self.s.selection.momentum_max_age_seconds), now, row["ticker"])
        if latest is None:
            return reject_flow("لا توجد إشارة مؤشر حديثة لنفس الرمز")
        if latest["direction"] != row["direction"]:
            return reject_flow(f"آخر إشارة مؤشر أصبحت {latest['direction']} بدل {row['direction']}")
        strength = _payload_strength(latest)
        if strength is None:
            return reject_flow("قيمة strength غير موجودة أو غير صالحة في تنبيه المؤشر")
        if strength < Decimal("65"):
            return reject_flow(f"قوة المؤشر {strength}% أقل من الحد الحالي 65%")
        snapshot = await self.liquidity.recent(
            self.s.selection.flow_window_minutes,
            premium_min=self.s.selection.premium_min,
            premium_max=self.s.selection.premium_max,
        )
        now = utcnow()
        age = (now - row["signal_time"]).total_seconds()
        if age > self.s.selection.momentum_max_age_seconds:
            return reject_flow(f"انتهت صلاحية الإشارة أثناء انتظار UW: {int(age)} ثانية")
        if not snapshot.complete:
            return reject_flow("بيانات UW غير مكتملة: " + (snapshot.reason or "UNKNOWN"))
        if snapshot.latest_at is None:
            return reject_flow("لا توجد تداولات مؤهلة لعقود اليوم في نافذة الرصد")
        if not snapshot.fresh(now):
            lag = int((now - snapshot.latest_at).total_seconds())
            return reject_flow(f"تدفقات UW غير حديثة: آخر تنفيذ قبل {lag} ثانية")
        net_call = sum((f.net_buy for f in snapshot.contracts.values() if f.direction == "CALL"), Decimal(0))
        net_put = sum((f.net_buy for f in snapshot.contracts.values() if f.direction == "PUT"), Decimal(0))
        directional = snapshot.market_directional if snapshot.market_directional is not None else net_call - net_put
        if (row["direction"] == "CALL" and directional <= 0
            or row["direction"] == "PUT" and directional >= 0):
            scope = "UW العام" if snapshot.market_directional is not None else "عقود النطاق المرشحة"
            bias = "CALL" if directional > 0 else ("PUT" if directional < 0 else "محايد")
            return reject_flow(
                f"اتجاه {scope} خلال {self.s.selection.flow_window_minutes} دقائق: {bias} "
                f"(الصافي الاتجاهي {directional:+.2f} دولار)، لا يؤكد {row['direction']}. "
                f"للمقارنة فقط، صافي عقود النطاق: CALL={net_call}، PUT={net_put}"
            )
        return replace(snapshot, indicator_strength=strength)

    async def quote_matches_trigger(self, row, quote):
        if row.get("source") != "autonomous":
            return True
        return self.autonomous is not None and await self.autonomous.accepts_quote(row, quote)

    async def propose_alternative(self, proposal):
        """A new proposal requires a NEW owner approval; old buttons cannot approve it."""
        row = await self.db.fetchrow(q.GET_SIGNAL, proposal["signal_db_id"])
        if row.get("source") == "autonomous":
            return None  # This trigger authorizes only its individually verified contract.
        payload = dict(self._signal_payload(row))
        count = int(payload.get("alternative_count", 0))
        if count >= self.s.selection.alternative_attempts or await self._confirmed_flow(row) is None:
            return None
        payload["alternative_count"] = count + 1
        payload["publication_chat_id"] = proposal.get("publication_chat_id")
        payload["excluded_symbols"] = list(set(payload.get("excluded_symbols", [])) | {proposal["option_symbol"]})
        ident = f"{row['signal_id']}:alternative"
        assert self.db.pool is not None
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval(q.CANCEL_OVERPRICED_PROPOSAL, proposal["id"]) is None:
                    return None
                signal_id = await conn.fetchval(q.INSERT_SIGNAL, ident, "alternative",
                    row["direction"], row["event_type"], row["ticker"], row["timeframe"],
                    row["tv_price"], row["signal_time"], payload)
        return signal_id

    # -----------------------------------------------------
    async def _entry_loop(self, run_id: int, chosen, signal_time):
        e = self.s.entry
        first_ask = chosen.ask
        window_start = utcnow()
        latest_quote = chosen
        if self.s.flags.flow_selection:
            quotes, _ = await self.uw.quotes_for([chosen.symbol])
            latest_quote = quotes.get(chosen.symbol)
        history = [EntryObservation(window_start,
                    latest_quote.bid if latest_quote else None,
                    latest_quote.ask if latest_quote else None)]
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
                highest_bid = max(
                    (observation.bid for observation in history if observation.bid is not None),
                    default=None,
                )
                return result, latest_quote, highest_bid
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
        highest_bid = max(
            (observation.bid for observation in history if observation.bid is not None),
            default=None,
        )
        return final, latest_quote, highest_bid

    async def _open_strikes(self, direction):
        rows = await self.db.fetch(q.OPEN_DIRECTION_STRIKES, direction.value)
        return {Decimal(str(row["strike"])) for row in rows}

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

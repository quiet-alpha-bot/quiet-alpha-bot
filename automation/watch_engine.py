"""UW WATCH engine: independent market observation (Shadow / intelligence).

Runs on the leader instance only, on its own interval, with no dependency
on TradingView. In v1 a watch NEVER creates a proposal or a trade.
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
from .matching import late_observation
from .models import Classification, Direction, WatchStatus
from .telegram_ui import watch_notification
from .timeutil import utcnow, within_session
from .uw_client import UWClient, UWError
from .watch_logic import evaluate_flow, watch_key

logger = logging.getLogger("quiet-alpha.watch")


class WatchEngine:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        uw: UWClient,
        publisher: Publisher,
    ) -> None:
        self.s = settings
        self.db = db
        self.uw = uw
        self.publisher = publisher
        self.last_ok_at = None
        self.last_error: Optional[str] = None

    async def run(self) -> None:
        logger.info("UW watch engine started (shadow)")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("UW watch tick failed")
            await asyncio.sleep(self.s.watch.poll_seconds)

    async def expire_due(self) -> None:
        """Watches whose window closed with no indicator signal -> UW_ONLY."""
        cutoff = utcnow() - timedelta(seconds=self.s.match.expiry_grace_seconds)
        rows = await self.db.fetch(q.EXPIRE_WATCHES, cutoff)
        for row in rows:
            await self.db.fetchval(
                q.INSERT_MATCH,
                Classification.UW_ONLY.value,
                None,
                row["id"],
                None,
                row["direction"],
                None,
                row["watch_time"],
                None,
                [],
                True,
                self.s.match.rules_version,
                self.s.match.window_minutes,
                utcnow(),
            )
            logger.info("Watch #%s closed as UW_ONLY", row["id"])

    async def tick(self) -> None:
        if self.s.flags.matching:
            await self.expire_due()

        now = utcnow()
        if not within_session(now, self.s.market.open_et, self.s.market.close_et):
            return

        w = self.s.watch
        alerts = None
        try:
            ticks = await self.uw.net_prem_ticks(w.ticker)
        except UWError as error:
            self.last_error = error.kind
            await self.db.fetchval(
                q.INSERT_OBSERVATION, w.ticker, None, now, None, None, None,
                False, {}, f"net_prem_ticks:{error.kind}",
            )
            logger.warning("UW watch: net-prem-ticks failed (%s)", error.kind)
            return

        if w.use_flow_alerts:
            try:
                alerts = await self.uw.flow_alerts(w.ticker, w.flow_alerts_limit)
            except UWError as error:
                logger.info("UW watch: flow-alerts unavailable (%s)", error.kind)

        evaluation = evaluate_flow(
            ticks, now, w.window_minutes, w.min_net_premium, w.min_strength, alerts
        )
        observation_id = await self.db.fetchval(
            q.INSERT_OBSERVATION,
            w.ticker,
            evaluation.window_start,
            evaluation.window_end,
            evaluation.directional_premium,
            evaluation.strength,
            evaluation.direction.value if evaluation.direction else None,
            evaluation.triggered,
            evaluation.metrics,
            None,
        )
        self.last_ok_at = now
        self.last_error = None

        if evaluation.triggered and evaluation.direction is not None:
            await self._maybe_create_watch(evaluation, observation_id, now)

    async def _maybe_create_watch(self, evaluation, observation_id: int, now) -> None:
        direction: Direction = evaluation.direction
        cooldown_start = now - timedelta(minutes=self.s.watch.cooldown_minutes)
        recent = await self.db.fetchval(
            q.RECENT_WATCH_SAME_DIRECTION, direction.value, cooldown_start
        )
        if recent is not None:
            return

        expires_at = now + timedelta(minutes=self.s.match.window_minutes)
        row = await self.db.fetchrow(
            q.INSERT_WATCH,
            watch_key(direction, now, self.s.watch.cooldown_minutes),
            direction.value,
            now,
            expires_at,
            evaluation.directional_premium,
            evaluation.strength,
            evaluation.metrics,
            observation_id,
            {
                "watch": strategy_snapshot(self.s)["watch"],
                "match_window_minutes": self.s.match.window_minutes,
            },
        )
        if row is None:
            return  # another instance created it

        watch_id = row["id"]
        logger.info(
            "UW WATCH #%s %s net=%s strength=%s",
            watch_id,
            direction.value,
            evaluation.directional_premium,
            evaluation.strength,
        )

        # Observation only: indicator signals that came BEFORE this watch.
        if self.s.flags.matching:
            window_start = now - timedelta(minutes=self.s.match.window_minutes)
            signals = await self.db.fetch(q.SIGNALS_BEFORE_WATCH, window_start, now)
            for signal in signals:
                await self.db.fetchval(
                    q.INSERT_MATCH,
                    late_observation(direction, Direction(signal["direction"])).value,
                    signal["id"],
                    watch_id,
                    signal["direction"],
                    direction.value,
                    signal["signal_time"],
                    now,
                    Decimal(str(round((signal["signal_time"] - now).total_seconds(), 3))),
                    [],
                    True,
                    self.s.match.rules_version,
                    self.s.match.window_minutes,
                    now,
                )

        if self.s.flags.uw_watch_notify:
            await self.publisher.text_once(
                "watch",
                watch_id,
                "WATCH_NOTICE",
                watch_notification(
                    direction.value,
                    evaluation.directional_premium,
                    evaluation.strength,
                    expires_at,
                    self.s.market.display_timezone,
                ),
            )


__all__ = ["WatchEngine", "WatchStatus"]

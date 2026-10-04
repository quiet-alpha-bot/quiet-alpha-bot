"""Entry selection: ENTER / WAIT / SKIP (pure, no look-ahead).

The decision uses only observations collected up to the decision time.
Every number below is a DEVELOPMENT / SHADOW DEFAULT from config.py.

Order of checks for the latest observation:
  1. past LAST_ENTRY_TIME_ET               -> SKIP
  2. signal older than MAX_SIGNAL_AGE      -> SKIP
  3. ask above first ask by CHASE_LIMIT    -> SKIP
  4. invalid quote / ask outside range     -> WAIT (or SKIP when window ends)
  5. spread wider than WAIT_SPREAD_PCT or
     ask moved more than STABILITY_MAX_MOVE
     since the previous observation        -> WAIT (window end -> ON_WINDOW_END)
  6. otherwise                             -> ENTER at the current Ask
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence

from .config import EntrySettings, SelectionSettings
from .models import EntryDecision
from .timeutil import to_et


@dataclass(frozen=True)
class EntryObservation:
    at: datetime
    bid: Optional[Decimal]
    ask: Optional[Decimal]

    @property
    def spread_pct(self) -> Optional[Decimal]:
        if self.bid is None or self.ask is None:
            return None
        mid = (self.bid + self.ask) / 2
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid

    @property
    def valid(self) -> bool:
        return (
            self.bid is not None
            and self.ask is not None
            and self.ask > 0
            and self.bid >= 0
            and self.ask >= self.bid
        )


@dataclass(frozen=True)
class EntryResult:
    decision: EntryDecision
    reason: str
    price: Optional[Decimal] = None


def decide_entry(
    history: Sequence[EntryObservation],
    first_ask: Decimal,
    signal_time: datetime,
    window_started_at: datetime,
    now: datetime,
    entry: EntrySettings,
    selection: SelectionSettings,
) -> EntryResult:
    if not history:
        return EntryResult(EntryDecision.WAIT, "NO_OBSERVATION")

    latest = history[-1]
    window_over = (now - window_started_at).total_seconds() >= entry.window_seconds

    if entry.last_entry_time_et is not None:
        if to_et(now).time() >= entry.last_entry_time_et:
            return EntryResult(EntryDecision.SKIP, "PAST_LAST_ENTRY_TIME")

    if entry.max_signal_age_seconds is not None:
        age = (now - signal_time).total_seconds()
        if age > entry.max_signal_age_seconds:
            return EntryResult(EntryDecision.SKIP, "SIGNAL_TOO_OLD")

    if (
        latest.valid
        and entry.chase_limit_pct is not None
        and latest.ask > first_ask * (1 + entry.chase_limit_pct)
    ):
        return EntryResult(EntryDecision.SKIP, "CHASE_LIMIT")

    if not latest.valid:
        if window_over:
            return EntryResult(EntryDecision.SKIP, "INVALID_QUOTE_AT_WINDOW_END")
        return EntryResult(EntryDecision.WAIT, "INVALID_QUOTE")

    if not (selection.premium_min <= latest.ask <= selection.premium_max):
        if window_over:
            return EntryResult(EntryDecision.SKIP, "PREMIUM_OUT_OF_RANGE")
        return EntryResult(EntryDecision.WAIT, "PREMIUM_OUT_OF_RANGE")

    wait_reasons: list[str] = []
    spread_pct = latest.spread_pct
    if (
        entry.wait_spread_pct is not None
        and spread_pct is not None
        and spread_pct > entry.wait_spread_pct
    ):
        wait_reasons.append("SPREAD_WIDE")

    if entry.stability_max_move is not None:
        previous_ask = first_ask
        for obs in reversed(history[:-1]):
            if obs.valid:
                previous_ask = obs.ask
                break
        if abs(latest.ask - previous_ask) > entry.stability_max_move:
            wait_reasons.append("UNSTABLE")

    if wait_reasons:
        reason = "+".join(wait_reasons)
        if not window_over:
            return EntryResult(EntryDecision.WAIT, reason)
        if entry.on_window_end == "ENTER":
            return EntryResult(
                EntryDecision.ENTER, f"WINDOW_END_ENTER({reason})", latest.ask
            )
        return EntryResult(EntryDecision.SKIP, f"WINDOW_END_SKIP({reason})")

    return EntryResult(EntryDecision.ENTER, "CONDITIONS_MET", latest.ask)


def within_tolerance(
    suggested: Decimal,
    current: Decimal,
    tolerance_abs: Optional[Decimal],
    tolerance_pct: Optional[Decimal],
) -> bool:
    """Approval repricing check. With no tolerance configured, any change
    requires re-confirmation."""
    diff = abs(current - suggested)
    if tolerance_abs is None and tolerance_pct is None:
        return diff == 0
    if tolerance_abs is not None and diff <= tolerance_abs:
        return True
    if tolerance_pct is not None and suggested > 0 and diff <= suggested * tolerance_pct:
        return True
    return False

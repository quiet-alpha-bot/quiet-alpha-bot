"""Indicator / UW watch classification (pure, no look-ahead).

Rules (window W = MATCH_WINDOW_MINUTES, a DEVELOPMENT / SHADOW DEFAULT):

* A signal at time T only looks at watches with  T - W <= watch_time <= T.
  Anything that happens after T is never used to classify the signal.
* same-direction watch(es) only        -> BOTH_CONFIRMED
* opposite-direction watch(es) only    -> CONFLICT
* both directions inside the window    -> CONFLICT   (policy "CONFLICT")
                                          or the most recent watch decides
                                          (policy "LATEST")
* no watch                             -> INDICATOR_ONLY
* a watch whose window closed (watch_time + W) with no indicator signal
  of either direction                  -> UW_ONLY  (decided at window close)
* a watch created AFTER a signal is an observation only
  (LATE_UW_AGREEMENT / LATE_UW_DISAGREEMENT); it never rewrites the
  signal's original classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from .models import Classification, Direction


@dataclass(frozen=True)
class WatchRef:
    id: int
    direction: Direction
    watch_time: datetime


@dataclass(frozen=True)
class MatchResult:
    classification: Classification
    primary_watch: Optional[WatchRef]
    same_direction: tuple[WatchRef, ...]
    opposite_direction: tuple[WatchRef, ...]

    @property
    def related_ids(self) -> list[int]:
        return [w.id for w in self.same_direction + self.opposite_direction]

    def time_difference_seconds(self, signal_time: datetime) -> Optional[float]:
        """Positive value = UW watch came BEFORE the indicator signal."""
        if self.primary_watch is None:
            return None
        return (signal_time - self.primary_watch.watch_time).total_seconds()


def classify_signal(
    signal_direction: Direction,
    signal_time: datetime,
    watches: Sequence[WatchRef],
    window: timedelta,
    mixed_policy: str = "CONFLICT",
) -> MatchResult:
    earliest = signal_time - window
    eligible = [
        w for w in watches if earliest <= w.watch_time <= signal_time
    ]
    eligible.sort(key=lambda w: (w.watch_time, w.id))

    same = tuple(w for w in eligible if w.direction == signal_direction)
    opposite = tuple(w for w in eligible if w.direction != signal_direction)

    if not same and not opposite:
        return MatchResult(Classification.INDICATOR_ONLY, None, (), ())

    if same and not opposite:
        return MatchResult(Classification.BOTH_CONFIRMED, same[-1], same, opposite)

    if opposite and not same:
        return MatchResult(Classification.CONFLICT, opposite[-1], same, opposite)

    # Both directions inside the window.
    if mixed_policy == "LATEST":
        latest = eligible[-1]
        classification = (
            Classification.BOTH_CONFIRMED
            if latest.direction == signal_direction
            else Classification.CONFLICT
        )
        return MatchResult(classification, latest, same, opposite)

    return MatchResult(Classification.CONFLICT, opposite[-1], same, opposite)


def late_observation(
    watch_direction: Direction, signal_direction: Direction
) -> Classification:
    return (
        Classification.LATE_UW_AGREEMENT
        if watch_direction == signal_direction
        else Classification.LATE_UW_DISAGREEMENT
    )

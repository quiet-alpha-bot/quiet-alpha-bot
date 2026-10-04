"""Milestones, SUCCESS and highest tracking (pure).

Observed Bid (UW Basic) = latest nbbo_bid returned by the UW REST poll.
It is an observation, not a claim of executable size.

* +N% milestone n reached when  Observed Bid >= approved_entry * (1 + n/100)
* SUCCESS reached when           Observed Bid >= approved_entry + $1.00
  (+$100 gross per SPX/SPXW contract). Independent of the +10% ladder.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from typing import Optional

HUNDRED = Decimal(100)


def gain_pct(entry: Decimal, price: Decimal) -> Decimal:
    if entry <= 0:
        return Decimal(0)
    return (price - entry) / entry * HUNDRED


def milestone_level(entry: Decimal, bid: Optional[Decimal], step_pct: int) -> int:
    """Highest step level reached by this bid (0 if none)."""
    if bid is None or entry <= 0 or bid <= entry:
        return 0
    pct = gain_pct(entry, bid)
    steps = (pct / Decimal(step_pct)).to_integral_value(rounding=ROUND_FLOOR)
    return int(steps) * step_pct


def new_milestones(already_sent: int, reached: int, step_pct: int) -> list[int]:
    """Levels not sent yet, ascending. A drop and re-cross never repeats."""
    if reached <= already_sent:
        return []
    start = (already_sent // step_pct + 1) * step_pct
    return list(range(start, reached + 1, step_pct))


def success_price(entry: Decimal, gain: Decimal) -> Decimal:
    return entry + gain


def is_success(entry: Decimal, bid: Optional[Decimal], gain: Decimal) -> bool:
    return bid is not None and bid >= success_price(entry, gain)


def success_pct(entry: Decimal, gain: Decimal) -> Decimal:
    return gain_pct(entry, success_price(entry, gain))


def milestone_threshold(entry: Decimal, level: int) -> Decimal:
    return entry * (1 + Decimal(level) / HUNDRED)


@dataclass(frozen=True)
class PendingEvent:
    """A detected event waiting to be published.

    `bid` is the Observed Bid at which the crossing was DETECTED (the poll
    that first showed price at/above the threshold). It is not a claim that
    the contract traded exactly at the threshold.
    """

    kind: str          # "MILESTONE" or "SUCCESS"
    level: int         # milestone percent; 0 for SUCCESS
    threshold: Decimal
    bid: Optional[Decimal]


def pending_events(
    entry: Decimal,
    step_pct: int,
    highest_sent: int,
    highest_reached: int,
    detections: dict[int, Decimal],
    success_pending: bool,
    success_bid: Optional[Decimal],
    gain: Decimal,
) -> list[PendingEvent]:
    """Every detected-but-unsent event, ordered by threshold price.

    Example: entry 4.00, jump 4.20 -> 5.30 gives
    +10% (4.40), +20% (4.80), SUCCESS (5.00), +30% (5.20).
    On an exact tie SUCCESS is published before the milestone
    (entry 2.50: SUCCESS 3.50 then +40% 3.50).
    """
    events = [
        PendingEvent("MILESTONE", level, milestone_threshold(entry, level),
                     detections.get(level))
        for level in new_milestones(highest_sent, highest_reached, step_pct)
    ]
    if success_pending:
        events.append(
            PendingEvent("SUCCESS", 0, success_price(entry, gain), success_bid)
        )
    events.sort(key=lambda e: (e.threshold, 0 if e.kind == "SUCCESS" else 1))
    return events


@dataclass(frozen=True)
class TrackState:
    current_bid: Optional[Decimal]
    current_ask: Optional[Decimal]
    current_last: Optional[Decimal]
    highest_bid: Optional[Decimal]
    highest_bid_time: Optional[datetime]
    highest_mid: Optional[Decimal]
    highest_last: Optional[Decimal]
    lowest_bid: Optional[Decimal]
    lowest_bid_time: Optional[datetime]


def apply_observation(
    state: TrackState,
    bid: Optional[Decimal],
    ask: Optional[Decimal],
    last: Optional[Decimal],
    at: datetime,
) -> tuple[TrackState, bool]:
    """Fold one observation into the state. Returns (state, new_high_bid)."""
    new_high = False
    updated = replace(state, current_bid=bid, current_ask=ask, current_last=last)

    if bid is not None and bid > 0:
        if state.highest_bid is None or bid > state.highest_bid:
            updated = replace(updated, highest_bid=bid, highest_bid_time=at)
            new_high = True
        if state.lowest_bid is None or bid < state.lowest_bid:
            updated = replace(updated, lowest_bid=bid, lowest_bid_time=at)

    if bid is not None and ask is not None and ask >= bid > 0:
        mid = (bid + ask) / 2
        if state.highest_mid is None or mid > state.highest_mid:
            updated = replace(updated, highest_mid=mid)

    if last is not None and last > 0:
        if state.highest_last is None or last > state.highest_last:
            updated = replace(updated, highest_last=last)

    return updated, new_high


def progress_items(
    entry: Decimal,
    highest_milestone: int,
    step_pct: int,
    success_detected: bool,
    gain: Decimal,
) -> list[tuple[Decimal, str]]:
    """Reached items ordered by percentage, SUCCESS placed at its real level."""
    items: list[tuple[Decimal, str]] = [
        (Decimal(level), f"+{level}%")
        for level in range(step_pct, highest_milestone + 1, step_pct)
    ]
    if success_detected:
        items.append((success_pct(entry, gain), "SUCCESS"))
    items.sort(key=lambda item: (item[0], item[1] != "SUCCESS"))
    return items

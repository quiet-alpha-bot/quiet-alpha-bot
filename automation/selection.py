"""SPXW contract selection: HARD FILTERS, then RANKING (pure).

Hard filters reject contracts that cannot be considered at all. Quality
thresholds (spread, volume, OI, delta, freshness) are OFF by default
(None) until real UW data is reviewed; quality is handled by ranking.

Ranking weights are DEVELOPMENT / SHADOW DEFAULTS. Every candidate, its
factor scores and its reject reasons are returned so they can be stored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Sequence

from .config import SelectionSettings
from .models import Direction, OptionQuote

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass
class Candidate:
    quote: OptionQuote
    reject_reasons: list[str] = field(default_factory=list)
    score: Optional[Decimal] = None
    breakdown: dict = field(default_factory=dict)
    rank: Optional[int] = None

    @property
    def passed(self) -> bool:
        return not self.reject_reasons


@dataclass
class SelectionResult:
    candidates: list[Candidate]
    selected: Optional[Candidate]

    @property
    def passed(self) -> list[Candidate]:
        return [c for c in self.candidates if c.passed]


def entry_reference(quote: OptionQuote) -> Optional[Decimal]:
    """Buy-side reference used for the premium range and the entry: Ask."""
    return quote.ask


def hard_filter(
    quote: OptionQuote,
    direction: Direction,
    expiry: date,
    root: str,
    settings: SelectionSettings,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    occ = quote.occ
    if occ.root != root:
        reasons.append("ROOT_MISMATCH")
    if occ.expiry != expiry:
        reasons.append("EXPIRY_MISMATCH")
    if occ.right != direction.value:
        reasons.append("TYPE_MISMATCH")

    bid, ask = quote.bid, quote.ask
    if bid is None or ask is None:
        reasons.append("NO_QUOTE")
        return reasons
    if ask <= 0 or bid < 0 or ask < bid:
        reasons.append("INVALID_QUOTE")
        return reasons
    if settings.require_positive_bid and bid <= 0:
        reasons.append("ZERO_BID")

    reference = entry_reference(quote)
    if reference is None or not (
        settings.premium_min <= reference <= settings.premium_max
    ):
        reasons.append("PREMIUM_OUT_OF_RANGE")

    spread = quote.spread
    spread_pct = quote.spread_pct
    if settings.max_spread_abs is not None and spread is not None:
        if spread > settings.max_spread_abs:
            reasons.append("SPREAD_ABS_TOO_WIDE")
    if settings.max_spread_pct is not None and spread_pct is not None:
        if spread_pct > settings.max_spread_pct:
            reasons.append("SPREAD_PCT_TOO_WIDE")
    if settings.min_volume is not None and (quote.volume or 0) < settings.min_volume:
        reasons.append("VOLUME_TOO_LOW")
    if (
        settings.min_open_interest is not None
        and (quote.open_interest or 0) < settings.min_open_interest
    ):
        reasons.append("OPEN_INTEREST_TOO_LOW")
    if settings.delta_min_abs is not None or settings.delta_max_abs is not None:
        if quote.delta is None:
            reasons.append("DELTA_MISSING")
        else:
            delta_abs = abs(quote.delta)
            if settings.delta_min_abs is not None and delta_abs < settings.delta_min_abs:
                reasons.append("DELTA_TOO_LOW")
            if settings.delta_max_abs is not None and delta_abs > settings.delta_max_abs:
                reasons.append("DELTA_TOO_HIGH")
    if settings.max_tape_age_seconds is not None:
        if quote.last_tape_time is None:
            reasons.append("NO_TAPE_TIME")
        elif (now - quote.last_tape_time).total_seconds() > settings.max_tape_age_seconds:
            reasons.append("STALE_TAPE")
    return reasons


def _minmax(values: list[Optional[float]], higher_is_better: bool) -> list[float]:
    present = [v for v in values if v is not None]
    if not present:
        return [0.5 for _ in values]
    low, high = min(present), max(present)
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
        elif high == low:
            out.append(1.0)
        else:
            norm = (v - low) / (high - low)
            out.append(norm if higher_is_better else 1.0 - norm)
    return out


def rank_candidates(
    passed: list[Candidate],
    settings: SelectionSettings,
    now: datetime,
    spot: Optional[Decimal],
) -> None:
    """Score passing candidates in place (0..1 per factor, weighted sum)."""
    if not passed:
        return

    spread_pct = [
        float(c.quote.spread_pct) if c.quote.spread_pct is not None else None
        for c in passed
    ]
    liquidity = [
        math.log1p(max(0, c.quote.volume or 0))
        + math.log1p(max(0, c.quote.open_interest or 0))
        for c in passed
    ]
    centre = float((settings.premium_min + settings.premium_max) / 2)
    half = float((settings.premium_max - settings.premium_min) / 2) or 1.0
    centrality = [
        max(0.0, 1.0 - abs(float(c.quote.ask) - centre) / half)
        if c.quote.ask is not None
        else 0.0
        for c in passed
    ]
    target = float(settings.delta_target_abs)
    delta_fit = [
        max(0.0, 1.0 - abs(abs(float(c.quote.delta)) - target) / max(target, 0.01))
        if c.quote.delta is not None
        else 0.5
        for c in passed
    ]
    tape_age = [
        (now - c.quote.last_tape_time).total_seconds()
        if c.quote.last_tape_time is not None
        else None
        for c in passed
    ]
    distance = [
        abs(float(c.quote.occ.strike) - float(spot)) if spot is not None else None
        for c in passed
    ]

    n_spread = _minmax(spread_pct, higher_is_better=False)
    n_liquidity = _minmax(liquidity, higher_is_better=True)
    n_fresh = _minmax(tape_age, higher_is_better=False)
    n_distance = _minmax(distance, higher_is_better=False)

    weights = {
        "spread": float(settings.weight_spread),
        "liquidity": float(settings.weight_liquidity),
        "centrality": float(settings.weight_centrality),
        "delta": float(settings.weight_delta),
        "freshness": float(settings.weight_freshness),
        "distance": float(settings.weight_distance) if spot is not None else 0.0,
    }
    weight_total = sum(weights.values()) or 1.0

    for i, cand in enumerate(passed):
        factors = {
            "spread": n_spread[i],
            "liquidity": n_liquidity[i],
            "centrality": centrality[i],
            "delta": delta_fit[i],
            "freshness": n_fresh[i],
            "distance": n_distance[i],
        }
        total = sum(factors[k] * weights[k] for k in factors) / weight_total
        cand.score = Decimal(str(round(total, 6)))
        cand.breakdown = {
            "factors": {k: round(v, 4) for k, v in factors.items()},
            "weights": weights,
            "weights_status": "DEVELOPMENT / SHADOW DEFAULTS",
        }

    def sort_key(c: Candidate):
        spread = c.quote.spread_pct if c.quote.spread_pct is not None else Decimal(99)
        delta_gap = (
            abs(abs(c.quote.delta) - settings.delta_target_abs)
            if c.quote.delta is not None
            else Decimal(99)
        )
        return (-(c.score or ZERO), spread, delta_gap, c.quote.symbol)

    passed.sort(key=sort_key)
    for position, cand in enumerate(passed, start=1):
        cand.rank = position


def select_contract(
    quotes: Sequence[OptionQuote],
    direction: Direction,
    expiry: date,
    root: str,
    settings: SelectionSettings,
    now: datetime,
    spot: Optional[Decimal] = None,
    excluded_strikes=(),
) -> SelectionResult:
    candidates = [
        Candidate(
            quote=q,
            reject_reasons=hard_filter(q, direction, expiry, root, settings, now),
        )
        for q in quotes
    ]
    blocked = set(excluded_strikes)
    for candidate in candidates:
        if candidate.quote.occ.strike in blocked:
            candidate.reject_reasons.append("STRIKE_ALREADY_OPEN")
    passed = [c for c in candidates if c.passed]
    rank_candidates(passed, settings, now, spot)
    selected = passed[0] if passed else None
    return SelectionResult(candidates=candidates, selected=selected)


def selection_reasons(result: SelectionResult) -> list[str]:
    """Short human-readable reasons for the proposal message."""
    chosen = result.selected
    if chosen is None:
        return []
    reasons = [f"أفضل ترتيب بين {len(result.passed)} عقد مؤهل"]
    q = chosen.quote
    if q.spread is not None:
        reasons.append(f"Spread ${q.spread.quantize(Decimal('0.01'))}")
    if q.volume is not None:
        reasons.append(f"Volume {q.volume:,}")
    if q.open_interest is not None:
        reasons.append(f"OI {q.open_interest:,}")
    if q.delta is not None:
        reasons.append(f"Delta {q.delta.quantize(Decimal('0.01'))}")
    return reasons


def select_with_flow(quotes, direction, expiry, root, settings, now, snapshot, spot=None, excluded_strikes=()):
    """Identical hard gates for original/alternative; strongest verified buy flow first."""
    from dataclasses import replace
    from .liquidity import eligible_flow
    quality = replace(
        settings,
        max_spread_abs=settings.max_spread_abs if settings.max_spread_abs is not None else Decimal("0.20"),
        max_spread_pct=settings.max_spread_pct if settings.max_spread_pct is not None else Decimal("0.15"),
        max_tape_age_seconds=settings.max_tape_age_seconds if settings.max_tape_age_seconds is not None else 120,
    )
    result = select_contract(quotes, direction, expiry, root, quality, now, spot, excluded_strikes=excluded_strikes)
    for cand in result.candidates:
        flow = snapshot.contracts.get(cand.quote.symbol)
        if not snapshot.fresh(now) or not eligible_flow(flow, now):
            cand.reject_reasons.append("NO_FRESH_REPEATED_BUY_FLOW")
        if flow is not None:
            cand.breakdown["executed_flow"] = {
                "premium": str(flow.premium), "net_buy": str(flow.net_buy),
                "volume": flow.volume, "minutes": len(flow.active_minutes),
                "start": snapshot.start.isoformat(), "end": snapshot.end.isoformat(),
            }
    passed = result.passed
    passed.sort(key=lambda c: (
        -snapshot.contracts[c.quote.symbol].net_buy,
        -snapshot.contracts[c.quote.symbol].premium,
        -(c.score or ZERO), c.quote.symbol,
    ))
    for cand in result.candidates:
        cand.rank = None
    for rank, cand in enumerate(passed, 1):
        cand.rank = rank
    result.selected = passed[0] if passed else None
    return result

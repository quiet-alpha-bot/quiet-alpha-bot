"""UW watch scoring (pure).

Input: UW "Call/Put Net/Vol Ticks" rows (one per minute) and, optionally,
UW flow alerts for the same ticker. Output: a direction score.

    net_call_premium = call premium ask-side - call premium bid-side
    net_put_premium  = put premium ask-side  - put premium bid-side
    directional      = net_call_premium - net_put_premium   (>0 bullish)
    strength         = directional / (|net_call| + |net_put|)   in [-1, 1]

A watch triggers when |directional| >= min_net_premium AND
|strength| >= min_strength. Both thresholds are DEVELOPMENT / SHADOW
DEFAULTS until real data is reviewed. Flow alerts are stored as context
only; they do not trigger a watch in v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from .models import Direction, dec, parse_ts, to_int

ZERO = Decimal(0)


@dataclass(frozen=True)
class WatchEvaluation:
    window_start: datetime
    window_end: datetime
    ticks_used: int
    net_call_premium: Decimal
    net_put_premium: Decimal
    directional_premium: Decimal
    strength: Decimal
    direction: Optional[Direction]
    triggered: bool
    metrics: dict = field(default_factory=dict)


def evaluate_flow(
    ticks: Sequence[dict],
    now: datetime,
    window_minutes: int,
    min_net_premium: Decimal,
    min_strength: Decimal,
    alerts: Optional[Sequence[dict]] = None,
) -> WatchEvaluation:
    window_start = now - timedelta(minutes=window_minutes)

    net_call = ZERO
    net_put = ZERO
    sums = {
        "call_volume": 0,
        "put_volume": 0,
        "call_volume_ask_side": 0,
        "call_volume_bid_side": 0,
        "put_volume_ask_side": 0,
        "put_volume_bid_side": 0,
    }
    net_delta = ZERO
    used = 0

    for row in ticks:
        tape_time = parse_ts(row.get("tape_time"))
        # Only completed information up to "now" (no look-ahead).
        if tape_time is None or tape_time < window_start or tape_time > now:
            continue
        used += 1
        net_call += dec(row.get("net_call_premium")) or ZERO
        net_put += dec(row.get("net_put_premium")) or ZERO
        net_delta += dec(row.get("net_delta")) or ZERO
        for key in sums:
            sums[key] += to_int(row.get(key)) or 0

    directional = net_call - net_put
    total = abs(net_call) + abs(net_put)
    strength = (directional / total) if total > 0 else ZERO

    direction: Optional[Direction] = None
    if directional > 0:
        direction = Direction.CALL
    elif directional < 0:
        direction = Direction.PUT

    triggered = (
        direction is not None
        and used > 0
        and abs(directional) >= min_net_premium
        and abs(strength) >= min_strength
    )

    metrics = {
        "ticks_used": used,
        "net_call_premium": str(net_call),
        "net_put_premium": str(net_put),
        "directional_premium": str(directional),
        "strength": str(strength.quantize(Decimal("0.0001"))) if total > 0 else "0",
        "net_delta": str(net_delta),
        **sums,
        "thresholds": {
            "min_net_premium": str(min_net_premium),
            "min_strength": str(min_strength),
            "window_minutes": window_minutes,
            "status": "DEVELOPMENT / SHADOW DEFAULTS",
        },
    }
    if alerts is not None:
        metrics["flow_alerts"] = summarize_alerts(alerts, window_start, now)

    return WatchEvaluation(
        window_start=window_start,
        window_end=now,
        ticks_used=used,
        net_call_premium=net_call,
        net_put_premium=net_put,
        directional_premium=directional,
        strength=strength,
        direction=direction,
        triggered=triggered,
        metrics=metrics,
    )


def summarize_alerts(
    alerts: Sequence[dict], window_start: datetime, now: datetime
) -> dict:
    summary = {
        "count": 0,
        "call_sweeps": 0,
        "put_sweeps": 0,
        "call_ask_side_premium": "0",
        "put_ask_side_premium": "0",
        "call_bid_side_premium": "0",
        "put_bid_side_premium": "0",
    }
    totals = {
        "call_ask": ZERO,
        "put_ask": ZERO,
        "call_bid": ZERO,
        "put_bid": ZERO,
    }
    for alert in alerts:
        created = parse_ts(alert.get("created_at"))
        if created is None or created < window_start or created > now:
            continue
        kind = str(alert.get("type", "")).lower()
        if kind not in {"call", "put"}:
            continue
        summary["count"] += 1
        if alert.get("has_sweep") in (True, "true", "True", 1):
            summary[f"{kind}_sweeps"] += 1
        totals[f"{kind}_ask"] += dec(alert.get("total_ask_side_prem")) or ZERO
        totals[f"{kind}_bid"] += dec(alert.get("total_bid_side_prem")) or ZERO
    summary["call_ask_side_premium"] = str(totals["call_ask"])
    summary["put_ask_side_premium"] = str(totals["put_ask"])
    summary["call_bid_side_premium"] = str(totals["call_bid"])
    summary["put_bid_side_premium"] = str(totals["put_bid"])
    return summary


def watch_key(direction: Direction, watch_time: datetime, cooldown_minutes: int) -> str:
    """Deterministic key so two instances cannot create the same watch."""
    bucket_seconds = max(60, cooldown_minutes * 60)
    bucket = int(watch_time.timestamp()) // bucket_seconds
    return f"{direction.value}:{bucket_seconds}:{bucket}"

"""Same-day SPXW executed-trade windows, shared by selection and radar.

Official source: /api/option-trades (ticker, DTE, newer/older cursors).
Size is the individual print size; `volume` is cumulative and must NOT
be summed across prints. Premium is executed notional, not resting orders.
Truncated pages, stale windows and unavailable data cannot select trades.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from .models import dec, parse_ts, to_int
from .occ import parse_occ
from .timeutil import et_date, utcnow
from .uw_client import UWError

ZERO = Decimal("0")


@dataclass
class ContractFlow:
    symbol: str
    strike: Decimal
    direction: str
    premium: Decimal = ZERO
    ask_premium: Decimal = ZERO
    bid_premium: Decimal = ZERO
    volume: int = 0
    prints: int = 0
    last_at: Optional[datetime] = None
    active_minutes: set = field(default_factory=set)

    @property
    def net_buy(self):
        return self.ask_premium - self.bid_premium


@dataclass
class FlowSnapshot:
    start: datetime
    end: datetime
    contracts: dict[str, ContractFlow]
    complete: bool
    fetched_at: datetime
    reason: Optional[str] = None

    @property
    def latest_at(self):
        return max((c.last_at for c in self.contracts.values() if c.last_at), default=None)

    def fresh(self, now: datetime, max_age: int = 120) -> bool:
        return (self.complete and self.latest_at is not None
                and 0 <= (now - self.latest_at).total_seconds() <= max_age)

    def strongest(self, direction: str, count: int = 3):
        items = [c for c in self.contracts.values() if c.direction == direction]
        return sorted(items, key=lambda c: (-c.premium, c.strike))[:count]


def aggregate_prints(rows, start, end, root="SPXW") -> FlowSnapshot:
    contracts = {}
    # Preserve cancellation information and deduplicate by exchange print id.
    unique = {}
    invalid = False
    for row in rows:
        ident = row.get("id")
        if ident is None:
            invalid = True
            continue
        if ident not in unique or row.get("canceled") in (True, "true"):
            unique[ident] = row
    for row in unique.values():
        if row.get("canceled") in (True, "true"):
            continue
        occ = parse_occ(row.get("option_chain_id") or row.get("option_symbol"))
        if occ is None or occ.root != root or occ.expiry != et_date(end):
            continue
        at = parse_ts(row.get("executed_at"))
        if at is None:
            invalid = True
            continue
        if not start <= at < end:
            continue
        premium, size = dec(row.get("premium")), to_int(row.get("size"))
        if premium is None or premium < 0 or size is None or size <= 0:
            invalid = True
            continue
        flow = contracts.setdefault(occ.symbol, ContractFlow(occ.symbol, occ.strike, occ.right))
        flow.premium += premium
        flow.volume += size
        flow.prints += 1
        flow.last_at = max(flow.last_at or at, at)
        flow.active_minutes.add(at.replace(second=0, microsecond=0))
        tags = row.get("tags") or []
        # A multileg print's side does not imply an outright directional buy.
        multi = any(tag in tags for tag in ("multileg", "multi_leg", "stock_multi_leg"))
        if not multi:
            if "ask_side" in tags and "bid_side" not in tags:
                flow.ask_premium += premium
            elif "bid_side" in tags and "ask_side" not in tags:
                flow.bid_premium += premium
    return FlowSnapshot(start, end, contracts, not invalid, utcnow(),
                        "INVALID_PRINTS" if invalid else None)


class LiquidityService:
    """Bounded pagination and a one-minute cache shared by all consumers."""
    def __init__(self, uw, max_pages: int = 20):
        self.uw = uw
        self.max_pages = max_pages
        self._cache = {}
        self._lock = asyncio.Lock()
        self.last_error = None

    async def snapshot(self, start: datetime, end: datetime, single_leg: bool = False) -> FlowSnapshot:
        # Cache key keeps interval endpoints: no leakage between hourly windows.
        if end <= start:
            return FlowSnapshot(start, end, {}, True, utcnow(), "WAITING_FOR_OPENING_PRINTS")
        key = (start, end, single_leg)
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (utcnow() - cached.fetched_at).total_seconds() < 60:
                return cached
            rows, complete, reason = [], False, None
            cursor = end
            previous_oldest = None
            try:
                for _ in range(self.max_pages):
                    params = {
                        "ticker_symbol": "SPX", "min_dte": 0, "max_dte": 0,
                        "limit": 500, "newer_than": start.isoformat(),
                        "older_than": cursor.isoformat(), "canceled": "false",
                    }
                    if single_leg:
                        params["is_multi_leg"] = "false"
                    response = await self.uw.get("/api/option-trades", params)
                    page = [r for r in self.uw._rows(response.data) if isinstance(r, dict)]
                    rows.extend(page)
                    if len(page) < 500:
                        complete = True
                        break
                    times = [parse_ts(r.get("executed_at")) for r in page]
                    if any(t is None for t in times):
                        reason = "MISSING_CURSOR_TIME"
                        break
                    oldest = min(times)
                    # Overlap the boundary timestamp and deduplicate ids so
                    # executions sharing a timestamp cannot disappear between pages.
                    if oldest >= cursor or (previous_oldest is not None and oldest >= previous_oldest):
                        reason = "AMBIGUOUS_CURSOR_BOUNDARY"
                        break
                    if oldest <= start:
                        complete = True
                        break
                    previous_oldest = oldest
                    cursor = oldest + timedelta(microseconds=1)
                if not complete and reason is None:
                    reason = "PAGE_LIMIT_REACHED"
            except UWError as error:
                reason = f"UW_{error.kind.upper()}"
            result = aggregate_prints(rows, start, end)
            result.complete = result.complete and complete
            result.reason = reason or result.reason
            self.last_error = result.reason
            self._cache = {k: v for k, v in self._cache.items()
                           if (utcnow() - v.fetched_at).total_seconds() < 60}
            self._cache[key] = result
            return result

    async def recent(self, minutes: int = 10):
        now = utcnow()
        end = now.replace(second=0, microsecond=0)
        return await self.snapshot(end - timedelta(minutes=minutes), end, single_leg=True)


def eligible_flow(flow, now, max_age=120):
    """Buy pressure must repeat, be recent and beat bid-side activity."""
    return (flow is not None and flow.net_buy > 0 and len(flow.active_minutes) >= 2
            and flow.last_at is not None
            and 0 <= (now - flow.last_at).total_seconds() <= max_age)

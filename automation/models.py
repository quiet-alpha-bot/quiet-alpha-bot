"""Shared value types, states and parsing helpers (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional

from .occ import OccSymbol, parse_occ


class Direction(str, Enum):
    CALL = "CALL"
    PUT = "PUT"

    @property
    def opposite(self) -> "Direction":
        return Direction.PUT if self is Direction.CALL else Direction.CALL


class Classification(str, Enum):
    INDICATOR_ONLY = "INDICATOR_ONLY"
    UW_ONLY = "UW_ONLY"
    BOTH_CONFIRMED = "BOTH_CONFIRMED"
    CONFLICT = "CONFLICT"
    # Observation only: a watch that appeared AFTER an indicator signal.
    # Never changes the signal's original classification.
    LATE_UW_AGREEMENT = "LATE_UW_AGREEMENT"
    LATE_UW_DISAGREEMENT = "LATE_UW_DISAGREEMENT"


class SignalStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    RECORDED = "RECORDED"        # stored/classified, selection disabled
    SELECTED = "SELECTED"        # contract+entry chosen, proposals disabled
    PROPOSED = "PROPOSED"
    NO_CONTRACT = "NO_CONTRACT"
    SKIPPED = "SKIPPED"
    DATA_ERROR = "DATA_ERROR"


class WatchStatus(str, Enum):
    INDICATOR_PENDING = "INDICATOR_PENDING"
    MATCHED = "MATCHED"
    CONFLICT = "CONFLICT"
    UW_ONLY = "UW_ONLY"


class ProposalStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVING = "APPROVING"          # transient while re-quoting
    NEEDS_RECONFIRM = "NEEDS_RECONFIRM"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TradeStatus(str, Enum):
    TRACKING = "TRACKING"
    CLOSED = "CLOSED"


class EntryDecision(str, Enum):
    ENTER = "ENTER"
    WAIT = "WAIT"
    SKIP = "SKIP"


# =========================================================
# Parsing helpers
# =========================================================


def dec(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "nan"}:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    if not result.is_finite():
        return None
    return result


def to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def parse_ts(value: Any) -> Optional[datetime]:
    """Parse ISO-8601 strings or unix seconds/milliseconds into aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().lstrip("-").isdigit()
    ):
        number = float(value)
        if number > 1e12:  # milliseconds
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def money(value: Optional[Decimal]) -> str:
    """Two-decimal text for messages, e.g. Decimal('4.4') -> '4.40'."""
    if value is None:
        return "—"
    return f"{value.quantize(Decimal('0.01'))}"


# =========================================================
# Option quote
# =========================================================


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    occ: OccSymbol
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    last: Optional[Decimal]
    volume: Optional[int]
    open_interest: Optional[int]
    delta: Optional[Decimal]
    implied_volatility: Optional[Decimal]
    last_tape_time: Optional[datetime]
    sweep_volume: Optional[int] = None
    ask_side_volume: Optional[int] = None
    bid_side_volume: Optional[int] = None

    @property
    def mid(self) -> Optional[Decimal]:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Optional[Decimal]:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def spread_pct(self) -> Optional[Decimal]:
        mid = self.mid
        spread = self.spread
        if mid is None or spread is None or mid <= 0:
            return None
        return spread / mid

    def as_record(self) -> dict:
        """JSON-safe snapshot for storage."""
        return {
            "symbol": self.symbol,
            "bid": None if self.bid is None else str(self.bid),
            "ask": None if self.ask is None else str(self.ask),
            "last": None if self.last is None else str(self.last),
            "volume": self.volume,
            "open_interest": self.open_interest,
            "delta": None if self.delta is None else str(self.delta),
            "iv": None
            if self.implied_volatility is None
            else str(self.implied_volatility),
            "last_tape_time": None
            if self.last_tape_time is None
            else self.last_tape_time.isoformat(),
            "sweep_volume": self.sweep_volume,
            "ask_side_volume": self.ask_side_volume,
            "bid_side_volume": self.bid_side_volume,
        }


def quote_from_uw_row(row: dict) -> Optional[OptionQuote]:
    """Build an OptionQuote from one /option-contracts row.

    Field names follow the official UW docs (nbbo_bid, nbbo_ask, last_price,
    last_tape_time, volume, open_interest, delta, implied_volatility ...).
    Returns None when the symbol cannot be parsed.
    """
    symbol = row.get("option_symbol")
    occ = parse_occ(symbol)
    if occ is None:
        return None
    return OptionQuote(
        symbol=occ.symbol,
        occ=occ,
        bid=dec(row.get("nbbo_bid")),
        ask=dec(row.get("nbbo_ask")),
        last=dec(row.get("last_price")),
        volume=to_int(row.get("volume")),
        open_interest=to_int(row.get("open_interest")),
        delta=dec(row.get("delta")),
        implied_volatility=dec(row.get("implied_volatility")),
        last_tape_time=parse_ts(row.get("last_tape_time")),
        sweep_volume=to_int(row.get("sweep_volume")),
        ask_side_volume=to_int(row.get("ask_volume")),
        bid_side_volume=to_int(row.get("bid_volume")),
    )

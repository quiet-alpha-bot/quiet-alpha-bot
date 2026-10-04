"""OCC / OSI option symbol helpers (format used by Unusual Whales).

Example: SPXW261005C05800000 -> root SPXW, 2026-10-05, CALL, strike 5800.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

_OCC_RE = re.compile(
    r"^(?P<root>[A-Z]{1,6})\s*(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<right>[CP])(?P<strike>\d{8})$"
)


@dataclass(frozen=True)
class OccSymbol:
    root: str
    expiry: date
    right: str  # "CALL" or "PUT"
    strike: Decimal

    @property
    def symbol(self) -> str:
        strike_int = int((self.strike * 1000).to_integral_value())
        return (
            f"{self.root}{self.expiry:%y%m%d}"
            f"{'C' if self.right == 'CALL' else 'P'}{strike_int:08d}"
        )


def parse_occ(symbol: Optional[str]) -> Optional[OccSymbol]:
    if not symbol:
        return None
    match = _OCC_RE.match(symbol.strip().upper())
    if not match:
        return None
    try:
        expiry = date(
            2000 + int(match.group("yy")),
            int(match.group("mm")),
            int(match.group("dd")),
        )
    except ValueError:
        return None
    strike = Decimal(int(match.group("strike"))) / Decimal(1000)
    return OccSymbol(
        root=match.group("root"),
        expiry=expiry,
        right="CALL" if match.group("right") == "C" else "PUT",
        strike=strike,
    )


def strike_text(strike: Decimal) -> str:
    """Plain strike string, e.g. Decimal('7690') -> '7690'."""
    if strike == strike.to_integral():
        return str(int(strike))
    return format(strike.normalize(), "f")

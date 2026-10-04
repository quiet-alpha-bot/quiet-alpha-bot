"""Unusual Whales REST client (the only market data provider).

* API key from UW_API_KEY only; it is sent as a Bearer header and is never
  logged, stored or included in exception text.
* Timeouts, retries with backoff, 429 handling using the x-uw-* headers.
* The latest rate-limit headers are kept in `last_rate` for /qa_status.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional, Sequence

import httpx

from .config import UWSettings
from .models import Direction, OptionQuote, dec, quote_from_uw_row

logger = logging.getLogger("quiet-alpha.uw")

RATE_HEADERS = (
    "x-uw-daily-req-count",
    "x-uw-token-req-limit",
    "x-uw-minute-req-counter",
    "x-uw-req-per-minute-remaining",
    "x-uw-req-per-minute-reset",
)


class UWError(Exception):
    def __init__(self, kind: str, message: str, status: Optional[int] = None):
        super().__init__(f"UW {kind}: {message}")
        self.kind = kind  # timeout | rate_limited | auth | http | network | parse
        self.status = status


@dataclass
class UWResponse:
    data: Any
    status: int
    latency_ms: int
    rate: dict = field(default_factory=dict)


def _reset_delay(raw: Optional[str], attempt: int) -> float:
    fallback = min(30.0, 2.0 * (2 ** attempt))
    if raw is None:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        return fallback
    if 0 < value <= 120:
        return value
    now = time.time()
    if value > 1e12:  # epoch milliseconds
        value /= 1000.0
    if value > now:
        return min(60.0, value - now)
    return fallback


class UWClient:
    def __init__(self, api_key: str, settings: UWSettings) -> None:
        self._api_key = api_key
        self._settings = settings
        self._client: Optional[httpx.AsyncClient] = None
        self.last_rate: dict = {}
        self.request_count = 0
        self.error_count = 0
        self.rate_limited_count = 0
        self._chain_ticker: Optional[str] = None

    def __repr__(self) -> str:
        return "UWClient(<redacted>)"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._settings.base_url,
            timeout=httpx.Timeout(self._settings.timeout_seconds),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                "User-Agent": "quiet-alpha-bot/1.0",
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -----------------------------------------------------
    # Core request with retries
    # -----------------------------------------------------
    async def get(self, path: str, params: Optional[dict] = None) -> UWResponse:
        assert self._client is not None, "UWClient.start() not called"
        attempts = self._settings.max_retries + 1
        last_error: Optional[UWError] = None

        for attempt in range(attempts):
            started = time.monotonic()
            try:
                response = await self._client.get(path, params=params)
            except httpx.TimeoutException:
                last_error = UWError("timeout", f"{path} timed out")
            except httpx.HTTPError as error:
                last_error = UWError("network", f"{path}: {type(error).__name__}")
            else:
                latency = int((time.monotonic() - started) * 1000)
                self.request_count += 1
                self.last_rate = {
                    key: response.headers.get(key)
                    for key in RATE_HEADERS
                    if response.headers.get(key) is not None
                }
                if response.status_code == 429:
                    self.rate_limited_count += 1
                    last_error = UWError("rate_limited", path, 429)
                    if attempt + 1 < attempts:
                        await asyncio.sleep(
                            _reset_delay(
                                response.headers.get("x-uw-req-per-minute-reset"),
                                attempt,
                            )
                        )
                    continue
                if response.status_code in (401, 403):
                    self.error_count += 1
                    raise UWError("auth", f"{path} -> {response.status_code}",
                                  response.status_code)
                if response.status_code >= 500:
                    last_error = UWError("http", f"{path} -> {response.status_code}",
                                         response.status_code)
                elif response.status_code >= 400:
                    self.error_count += 1
                    raise UWError("http", f"{path} -> {response.status_code}",
                                  response.status_code)
                else:
                    try:
                        payload = response.json()
                    except ValueError as error:
                        self.error_count += 1
                        raise UWError("parse", f"{path}: invalid JSON") from error
                    return UWResponse(
                        data=payload,
                        status=response.status_code,
                        latency_ms=latency,
                        rate=dict(self.last_rate),
                    )

            if attempt + 1 < attempts:
                await asyncio.sleep(min(10.0, 1.0 * (2 ** attempt)))

        self.error_count += 1
        assert last_error is not None
        raise last_error

    @staticmethod
    def _rows(payload: Any) -> list:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
            return []
        if isinstance(payload, list):
            return payload
        return []

    # -----------------------------------------------------
    # Option contracts
    # -----------------------------------------------------
    async def option_contracts(
        self,
        ticker: str,
        expiry: Optional[date] = None,
        option_type: Optional[str] = None,
        symbols: Optional[Sequence[str]] = None,
        page: int = 0,
        limit: int = 500,
    ) -> tuple[list[OptionQuote], UWResponse]:
        params: dict[str, Any] = {"limit": limit, "page": page}
        if expiry is not None:
            params["expiry"] = expiry.isoformat()
        if option_type is not None:
            params["option_type"] = option_type
        if symbols:
            params["option_symbol[]"] = list(symbols)
        response = await self.get(f"/api/stock/{ticker}/option-contracts", params)
        quotes = []
        for row in self._rows(response.data):
            if isinstance(row, dict):
                quote = quote_from_uw_row(row)
                if quote is not None:
                    quotes.append(quote)
        return quotes, response

    async def chain(
        self, direction: Direction, expiry: date
    ) -> tuple[Optional[str], list[OptionQuote], dict]:
        """Same-day chain for one side. Tries UW_CHAIN_TICKERS in order and
        keeps the first ticker that returns rows with the SPXW root."""
        tickers = list(self._settings.chain_tickers)
        if self._chain_ticker in tickers:
            tickers.remove(self._chain_ticker)
            tickers.insert(0, self._chain_ticker)

        meta: dict = {"tried": [], "pages": 0, "latency_ms": []}
        option_type = "call" if direction is Direction.CALL else "put"
        for ticker in tickers:
            collected: list[OptionQuote] = []
            try:
                for page in range(self._settings.chain_max_pages):
                    quotes, response = await self.option_contracts(
                        ticker, expiry=expiry, option_type=option_type, page=page
                    )
                    meta["pages"] += 1
                    meta["latency_ms"].append(response.latency_ms)
                    collected.extend(quotes)
                    if len(quotes) < 500:
                        break
            except UWError as error:
                meta["tried"].append({"ticker": ticker, "error": error.kind})
                if error.kind == "auth":
                    raise
                continue
            roots = [q for q in collected if q.occ.root == self._settings.contract_root]
            meta["tried"].append(
                {"ticker": ticker, "rows": len(collected), "root_rows": len(roots)}
            )
            if roots:
                self._chain_ticker = ticker
                return ticker, collected, meta
        return None, [], meta

    async def quotes_for(
        self, symbols: Sequence[str]
    ) -> tuple[dict[str, OptionQuote], Optional[UWResponse]]:
        """Current rows for specific contracts (tracking / repricing)."""
        if not symbols:
            return {}, None
        tickers = list(self._settings.chain_tickers)
        if self._chain_ticker in tickers:
            tickers.remove(self._chain_ticker)
            tickers.insert(0, self._chain_ticker)
        last_error: Optional[UWError] = None
        for ticker in tickers:
            try:
                quotes, response = await self.option_contracts(
                    ticker, symbols=symbols
                )
            except UWError as error:
                if error.kind == "auth":
                    raise
                last_error = error
                continue
            found = {q.symbol: q for q in quotes if q.symbol in set(symbols)}
            if found:
                self._chain_ticker = ticker
                return found, response
            last_error = None
        if last_error is not None:
            raise last_error
        return {}, None

    # -----------------------------------------------------
    # Flow / context
    # -----------------------------------------------------
    async def net_prem_ticks(self, ticker: str) -> list[dict]:
        response = await self.get(f"/api/stock/{ticker}/net-prem-ticks")
        return [row for row in self._rows(response.data) if isinstance(row, dict)]

    async def flow_alerts(self, ticker: str, limit: int = 100) -> list[dict]:
        response = await self.get(
            f"/api/stock/{ticker}/flow-alerts", {"limit": min(200, max(1, limit))}
        )
        return [row for row in self._rows(response.data) if isinstance(row, dict)]

    async def spot(self, ticker: str) -> Optional[Decimal]:
        """Best-effort underlying price (stock-state 'close'). Optional."""
        try:
            response = await self.get(f"/api/stock/{ticker}/stock-state")
        except UWError as error:
            if error.kind == "auth":
                raise
            return None
        rows = self._rows(response.data)
        if not rows:
            return None
        return dec(rows[0].get("close"))

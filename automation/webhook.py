"""TradingView webhook intake (aiohttp, same process as the bot).

POST {WEBHOOK_PATH}   (default /webhook/tradingview)
GET  /health

TradingView cannot send custom headers, so the secret travels inside the
JSON body as "secret". Expected alert message (JSON):

{
  "secret": "<TV_WEBHOOK_SECRET>",
  "direction": "CALL",                 // or "PUT"
  "signal_time": "{{time}}",           // bar time (ISO) or unix ms
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "price": {{close}},
  "event_type": "INITIAL",             // optional
  "signal_id": "..."                   // optional; derived if absent
}

If signal_id is absent it is derived deterministically from
ticker|timeframe|direction|signal_time|event_type, so a re-sent alert for
the same bar is recognised as a duplicate.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Awaitable, Callable, Optional

from aiohttp import web

from . import queries as q
from .db import Database
from .models import Direction, dec, parse_ts

logger = logging.getLogger("quiet-alpha.webhook")

DIRECTION_ALIASES = {
    "CALL": Direction.CALL,
    "C": Direction.CALL,
    "PUT": Direction.PUT,
    "P": Direction.PUT,
}


class SignalPayloadError(ValueError):
    pass


def parse_signal_payload(raw: dict) -> dict:
    """Validate and normalise a TradingView payload (secret already checked)."""
    direction_raw = str(raw.get("direction", "")).strip().upper()
    direction = DIRECTION_ALIASES.get(direction_raw)
    if direction is None:
        raise SignalPayloadError("direction must be CALL or PUT")

    signal_time = parse_ts(raw.get("signal_time") or raw.get("time"))
    if signal_time is None:
        raise SignalPayloadError("signal_time is required (ISO or unix ms)")

    ticker = str(raw.get("ticker") or "").strip() or None
    timeframe = str(raw.get("timeframe") or raw.get("interval") or "").strip() or None
    event_type = str(raw.get("event_type") or "").strip() or None

    signal_id = str(raw.get("signal_id") or "").strip()
    if not signal_id:
        signal_id = "|".join(
            [
                ticker or "NA",
                timeframe or "NA",
                direction.value,
                signal_time.isoformat(),
                event_type or "NA",
            ]
        )
    if len(signal_id) > 200:
        raise SignalPayloadError("signal_id too long")

    payload = {k: v for k, v in raw.items() if k != "secret"}
    return {
        "signal_id": signal_id,
        "direction": direction,
        "event_type": event_type,
        "ticker": ticker,
        "timeframe": timeframe,
        "tv_price": dec(raw.get("price")),
        "signal_time": signal_time,
        "payload": payload,
    }


class WebhookServer:
    def __init__(
        self,
        db: Database,
        secret: Optional[str],
        path: str,
        port: int,
        max_body_bytes: int,
        intake_enabled: bool,
        on_new_signal: Callable[[int], Awaitable[None]],
        health: Callable[[], dict],
    ) -> None:
        self._db = db
        self._secret = secret
        self._path = path
        self._port = port
        self._max_body = max_body_bytes
        self._intake_enabled = intake_enabled
        self._on_new_signal = on_new_signal
        self._health = health
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        app = web.Application(client_max_size=self._max_body)
        app.router.add_post(self._path, self._handle_signal)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/", self._handle_health)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host="0.0.0.0", port=self._port)
        await site.start()
        logger.info("Webhook server listening on port %s", self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, **self._health()})

    async def _handle_signal(self, request: web.Request) -> web.Response:
        if not self._intake_enabled or not self._secret:
            return web.json_response({"ok": False, "error": "intake disabled"}, status=503)

        try:
            body = await request.read()
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"ok": False, "error": "too large"}, status=413)
        try:
            raw = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Webhook rejected: body is not JSON")
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(raw, dict):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)

        provided = str(raw.get("secret", ""))
        if not hmac.compare_digest(provided.encode(), self._secret.encode()):
            logger.warning("Webhook rejected: bad secret from %s", request.remote)
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        try:
            signal = parse_signal_payload(raw)
        except SignalPayloadError as error:
            logger.warning("Webhook rejected: %s", error)
            return web.json_response({"ok": False, "error": str(error)}, status=400)

        try:
            signal_db_id = await self._db.fetchval(
                q.INSERT_SIGNAL,
                signal["signal_id"],
                "tradingview",
                signal["direction"].value,
                signal["event_type"],
                signal["ticker"],
                signal["timeframe"],
                signal["tv_price"],
                signal["signal_time"],
                signal["payload"],
            )
        except Exception:
            logger.exception("Webhook: database insert failed")
            return web.json_response({"ok": False, "error": "storage"}, status=500)

        if signal_db_id is None:
            logger.info("Duplicate signal ignored: %s", signal["signal_id"])
            return web.json_response({"ok": True, "duplicate": True})

        logger.info(
            "Signal stored #%s %s %s",
            signal_db_id,
            signal["direction"].value,
            signal["signal_id"],
        )
        await self._on_new_signal(signal_db_id)
        return web.json_response({"ok": True, "duplicate": False, "id": signal_db_id})

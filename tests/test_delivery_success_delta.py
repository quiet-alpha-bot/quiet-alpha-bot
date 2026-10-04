"""Acceptance tests for the three fixes (runs with plain unittest, no DB):

FIX 1  only SENT counts as published; FAILED blocks later events and is
       retried (also after a restart); UNCERTAIN is never treated as SENT.
FIX 2  SUCCESS is not detected/stored/sent while SUCCESS_CARD_ENABLED=false,
       and after enabling only NEW observations can trigger it.
FIX 3  a UW row without delta gives delta=None; selection only rejects for
       DELTA_MISSING when a delta filter is enabled.

The tracker runs against an in-memory fake of the few SQL statements it
uses (dispatched by statement identity); the real SQL is covered by
tests/test_integration_pg.py and tests/validate_sql.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _module in ("telegram", "aiohttp", "httpx", "asyncpg"):
    if importlib.util.find_spec(_module) is None:
        sys.path.insert(0, str(ROOT / "tests" / "stubs"))
        break

from telegram.error import BadRequest, TimedOut  # noqa: E402

from automation import config as cfg  # noqa: E402
from automation import queries as q  # noqa: E402
import automation.tracker as tracker_module  # noqa: E402
from automation.cards import CardRenderers, Publisher  # noqa: E402
from automation.db import Database  # noqa: E402
from automation.models import Direction, OptionQuote, quote_from_uw_row  # noqa: E402
from automation.occ import parse_occ  # noqa: E402
from automation.selection import select_contract  # noqa: E402
from automation.tracker import Tracker  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # Monday 11:00 ET
SYMBOL = "SPXW261005C07700000"


class Clock:
    now = T0


tracker_module.utcnow = lambda: Clock.now


def make_settings(success_card: bool):
    env = {
        "BOT_TOKEN": "x", "SIGNAL_CHAT_ID": "-100123", "OWNER_USER_ID": "1",
        "DATABASE_URL": "postgres://unused", "UW_API_KEY": "k",
        "TRACKING_ENABLED": "true", "MILESTONES_ENABLED": "true",
        "SUCCESS_CARD_ENABLED": "true" if success_card else "false",
    }
    saved = dict(os.environ)
    try:
        for key in list(os.environ):
            if key.isupper():
                os.environ.pop(key)
        os.environ.update(env)
        return cfg.load_settings()
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---------------------------------------------------------------------------
# In-memory fake of the statements used by Tracker / Publisher / ledger
# ---------------------------------------------------------------------------
class MemoryDB(Database):
    def __init__(self):  # no real connection
        self.pool = None
        self.trades: dict[int, dict] = {}
        self.events: dict[tuple, dict] = {}
        self._event_id = 0

    def add_trade(self, entry: str) -> dict:
        trade = {
            "id": 1, "option_symbol": SYMBOL, "direction": "CALL", "strike": D(7700),
            "expiry": date(2026, 10, 5), "approved_entry": D(entry),
            "suggested_entry": D(entry), "classification": "INDICATOR_ONLY",
            "status": "TRACKING", "current_bid": None, "current_ask": None,
            "current_last": None, "highest_bid": None, "highest_bid_time": None,
            "highest_mid": None, "highest_last": None, "lowest_bid": None,
            "lowest_bid_time": None, "last_poll_at": None, "last_tape_time": None,
            "highest_milestone_sent": 0, "highest_milestone_reached": 0,
            "milestone_detections": {}, "success_detected_at": None,
            "success_bid": None, "success_sent": False,
            "tracking_message_id": 500, "tracking_message_edited_at": None,
        }
        self.trades[1] = trade
        return trade

    def status(self, event_type, level=0):
        event = self.events.get(("trade", 1, event_type, level))
        return None if event is None else event["status"]

    async def _dispatch(self, sql, args):
        t = self.trades.get(args[0]) if args and isinstance(args[0], int) else None
        if sql is q.GET_TRADE:
            return [dict(t)] if t else []
        if sql is q.TRACKING_TRADES:
            return [dict(x) for x in self.trades.values() if x["status"] == "TRACKING"]
        if sql is q.EVENT_STATUS:
            event = self.events.get(tuple(args))
            return [dict(event)] if event else []
        if sql is q.CLAIM_EVENT:
            key = tuple(args[:4])
            if key in self.events:
                return []
            self._event_id += 1
            self.events[key] = {"id": self._event_id, "status": "PENDING",
                                "attempts": 1, "message_id": None}
            return [{"id": self._event_id}]
        if sql is q.RECLAIM_FAILED_EVENT:
            event = self.events.get(tuple(args[:4]))
            if event and event["status"] == "FAILED" and event["attempts"] < args[4]:
                event["status"] = "PENDING"
                event["attempts"] += 1
                return [{"id": event["id"]}]
            return []
        if sql is q.MARK_EVENT:
            for event in self.events.values():
                if event["id"] == args[0]:
                    event.update(status=args[1], message_id=args[2])
            return []
        if sql is q.SET_TRADE_MILESTONE:
            t["highest_milestone_sent"] = max(t["highest_milestone_sent"], args[1])
            return []
        if sql is q.MARK_SUCCESS_SENT:
            t["success_sent"] = True
            return []
        if sql is q.RECORD_MILESTONE_DETECTIONS:
            t["highest_milestone_reached"] = max(t["highest_milestone_reached"], args[1])
            t["milestone_detections"] = {**args[2], **t["milestone_detections"]}
            return []
        if sql is q.MARK_SUCCESS_DETECTED:
            if t["success_detected_at"] is None:
                t["success_detected_at"], t["success_bid"] = args[1], args[2]
                return [{"id": t["id"]}]
            return []
        if sql is q.UPDATE_TRADE_QUOTE:
            keys = ["current_bid", "current_ask", "current_last", "highest_bid",
                    "highest_bid_time", "highest_mid", "highest_last", "lowest_bid",
                    "lowest_bid_time", "last_poll_at", "last_tape_time"]
            t.update(dict(zip(keys, args[1:])))
            return []
        if sql is q.TOUCH_TRACKING_MESSAGE:
            t["tracking_message_edited_at"] = Clock.now
            return []
        if sql in (q.INSERT_PRICE_OBSERVATION, q.SET_TRADE_ERROR, q.SET_TRACKING_MESSAGE):
            return []
        raise AssertionError("unexpected SQL in test: " + sql[:60])

    async def fetch(self, sql, *args):
        return await self._dispatch(sql, args)

    async def fetchrow(self, sql, *args):
        rows = await self._dispatch(sql, args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        row = await self.fetchrow(sql, *args)
        return None if row is None else next(iter(row.values()))

    async def execute(self, sql, *args):
        await self._dispatch(sql, args)
        return "OK"


class FakeBot:
    def __init__(self):
        self.log = []          # delivered messages, in order
        self.fail_rules = []   # [substring, exception, remaining_count]
        self.next_id = 1000

    def _maybe_fail(self, label):
        for rule in self.fail_rules:
            if rule[0] in label and rule[2] > 0:
                rule[2] -= 1
                raise rule[1]

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self._maybe_fail(text)
        self.next_id += 1
        self.log.append(text.split("|")[1].strip() if "|" in text else text)
        return type("M", (), {"message_id": self.next_id})()

    async def send_photo(self, chat_id, photo, **kwargs):
        self._maybe_fail(photo.name)
        self.next_id += 1
        self.log.append(photo.name)
        return type("M", (), {"message_id": self.next_id})()

    async def edit_message_text(self, **kwargs):
        return True


def renderers():
    def card(name):
        def make(**kwargs):
            buffer = BytesIO(b"x")
            buffer.name = name
            return buffer
        return make

    return CardRenderers(
        create_signal_card=card("ENTRY_CARD"),
        create_success_card=card("SUCCESS_CARD"),
        create_highest_price_card=card("HIGHEST_CARD"),
        create_stop_loss_card=card("STOP"),
        calculate_highest_result=lambda **k: (0, 0),
        format_strike=lambda v: v,
        format_premium=lambda v: f"{float(v):.2f}",
    )


def quote(bid: str) -> OptionQuote:
    b = D(bid)
    return OptionQuote(
        symbol=SYMBOL, occ=parse_occ(SYMBOL), bid=b, ask=b + D("0.10"), last=None,
        volume=10, open_interest=10, delta=None, implied_volatility=None,
        last_tape_time=None,
    )


class Harness:
    def __init__(self, db: MemoryDB, bot: FakeBot, success_card: bool):
        self.db = db
        self.bot = bot
        self.tracker = Tracker(
            make_settings(success_card), db, None,
            Publisher(bot, "-100123", db), renderers(),
        )

    async def poll(self, bid: str):
        Clock.now += timedelta(seconds=15)
        await self.tracker._process(dict(self.db.trades[1]), quote(bid), 5)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
class Fix1DeliveryTests(unittest.TestCase):
    def test_1_failed_milestone_blocks_later_events_then_retries_in_order(self):
        async def scenario():
            db, bot = MemoryDB(), FakeBot()
            db.add_trade("4.00")
            h = Harness(db, bot, success_card=True)
            # +20% fails 4 times (more than the old 3-attempt cap).
            bot.fail_rules.append(["+20%", BadRequest("down"), 4])

            await h.poll("4.20")
            await h.poll("5.30")  # crosses +10, +20, SUCCESS, +30

            self.assertEqual(db.status("MILESTONE", 10), "SENT")
            self.assertEqual(db.status("MILESTONE", 20), "FAILED")
            self.assertIsNone(db.status("SUCCESS_CARD"))
            self.assertIsNone(db.status("MILESTONE", 30))
            self.assertEqual(db.trades[1]["highest_milestone_sent"], 10)
            self.assertFalse(db.trades[1]["success_sent"])
            self.assertEqual(bot.log, ["+10%"])

            for _ in range(3):  # still failing: nothing moves past +20%
                await h.poll("4.50")
                self.assertEqual(bot.log, ["+10%"])
                self.assertEqual(db.trades[1]["highest_milestone_sent"], 10)

            await h.poll("4.50")  # Telegram is back -> +20, SUCCESS, +30
            self.assertEqual(bot.log, ["+10%", "+20%", "SUCCESS_CARD", "+30%"])
            self.assertEqual(db.trades[1]["highest_milestone_sent"], 30)
            self.assertTrue(db.trades[1]["success_sent"])
            for key in [("MILESTONE", 10), ("MILESTONE", 20), ("SUCCESS_CARD", 0),
                        ("MILESTONE", 30)]:
                self.assertEqual(db.status(*key), "SENT")

            await h.poll("4.10")
            await h.poll("5.40")  # drop and re-cross: nothing repeats
            self.assertEqual(bot.log, ["+10%", "+20%", "SUCCESS_CARD", "+30%"])

        run(scenario())

    def test_2_restart_recovery_retries_pending_without_duplicates(self):
        async def scenario():
            db, bot = MemoryDB(), FakeBot()
            db.add_trade("4.00")
            first = Harness(db, bot, success_card=True)
            bot.fail_rules.append(["+20%", BadRequest("down"), 1])
            await first.poll("4.20")
            await first.poll("5.30")
            self.assertEqual(bot.log, ["+10%"])
            self.assertEqual(db.status("MILESTONE", 20), "FAILED")

            # "Restart": new Tracker + Publisher from the same DB state.
            restarted = Harness(db, bot, success_card=True)
            await restarted.poll("4.30")  # price lower than before
            self.assertEqual(bot.log, ["+10%", "+20%", "SUCCESS_CARD", "+30%"])

            again = Harness(db, bot, success_card=True)
            await again.poll("4.30")
            self.assertEqual(bot.log.count("+10%"), 1)
            self.assertEqual(bot.log.count("+20%"), 1)
            self.assertEqual(bot.log.count("SUCCESS_CARD"), 1)
            self.assertEqual(bot.log.count("+30%"), 1)

        run(scenario())

    def test_uncertain_is_not_sent_and_blocks_later_events(self):
        async def scenario():
            db, bot = MemoryDB(), FakeBot()
            db.add_trade("4.00")
            h = Harness(db, bot, success_card=True)
            bot.fail_rules.append(["+20%", TimedOut("timeout"), 1])
            await h.poll("4.20")
            await h.poll("5.30")
            await h.poll("5.30")
            await Harness(db, bot, success_card=True).poll("5.30")  # restart
            self.assertEqual(db.status("MILESTONE", 20), "UNCERTAIN")
            self.assertIsNone(db.status("SUCCESS_CARD"))
            self.assertIsNone(db.status("MILESTONE", 30))
            self.assertEqual(db.trades[1]["highest_milestone_sent"], 10)
            sent = [x for x in bot.log if not x.startswith("⚠️")]
            self.assertEqual(sent, ["+10%"])  # +20 never resent, nothing after it

        run(scenario())


class Fix2SuccessFlagTests(unittest.TestCase):
    def test_3_success_disabled_records_nothing(self):
        async def scenario():
            db, bot = MemoryDB(), FakeBot()
            db.add_trade("3.40")
            h = Harness(db, bot, success_card=False)
            await h.poll("4.60")
            t = db.trades[1]
            self.assertIsNone(t["success_detected_at"])
            self.assertIsNone(t["success_bid"])
            self.assertIsNone(db.status("SUCCESS_CARD"))
            self.assertNotIn("SUCCESS_CARD", bot.log)
            self.assertFalse(t["success_sent"])
            # milestones are unaffected: 4.60 = +35.3% -> +10, +20, +30
            self.assertEqual(bot.log, ["+10%", "+20%", "+30%"])
            return db, bot

        run(scenario())

    def test_4_enable_success_later_uses_new_observations_only(self):
        async def scenario():
            db, bot = MemoryDB(), FakeBot()
            db.add_trade("3.40")
            await Harness(db, bot, success_card=False).poll("4.60")  # disabled

            enabled = Harness(db, bot, success_card=True)  # redeploy with flag on
            await enabled.poll("4.30")  # below 4.40: the earlier 4.60 is ignored
            self.assertIsNone(db.trades[1]["success_detected_at"])
            self.assertNotIn("SUCCESS_CARD", bot.log)

            await enabled.poll("4.45")  # new observation after enabling
            self.assertEqual(db.trades[1]["success_bid"], D("4.45"))
            self.assertEqual(db.status("SUCCESS_CARD"), "SENT")
            self.assertEqual(bot.log.count("SUCCESS_CARD"), 1)

        run(scenario())


class Fix3DeltaTests(unittest.TestCase):
    row = {
        "option_symbol": "SPXW261005C07740000",
        "nbbo_bid": "3.30",
        "nbbo_ask": "3.40",
        "volume": 100,
        "open_interest": 50,
    }

    def test_5_delta_missing(self):
        quote_ = quote_from_uw_row(self.row)
        self.assertIsNone(quote_.delta)

        base = make_settings(True).selection
        result = select_contract(
            [quote_], Direction.CALL, date(2026, 10, 5), "SPXW", base, T0
        )
        self.assertIsNotNone(result.selected)  # not rejected for missing delta
        self.assertEqual(result.candidates[0].reject_reasons, [])

        with_filter = replace(base, delta_min_abs=D("0.20"))
        result = select_contract(
            [quote_], Direction.CALL, date(2026, 10, 5), "SPXW", with_filter, T0
        )
        self.assertIsNone(result.selected)
        self.assertEqual(result.candidates[0].reject_reasons, ["DELTA_MISSING"])

        only_max = replace(base, delta_max_abs=D("0.60"))
        result = select_contract(
            [quote_], Direction.CALL, date(2026, 10, 5), "SPXW", only_max, T0
        )
        self.assertEqual(result.candidates[0].reject_reasons, ["DELTA_MISSING"])


if __name__ == "__main__":
    unittest.main()

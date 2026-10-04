"""End-to-end scenario against a REAL PostgreSQL, with Telegram/UW faked.

Exercises the real SQL and the real pipeline / approval / tracker code:
signal -> classification -> selection -> entry -> proposal -> APPROVE
(re-quote) -> entry card -> tracking -> +10% -> SUCCESS card -> restart ->
no duplicates; plus REJECT, RECONFIRM/CANCEL, unauthorized clicks,
duplicate webhook, UW_ONLY expiry and Telegram timeout (UNCERTAIN).

Runs only when QA_TEST_PSQL_ARGS is set, e.g.
    QA_TEST_PSQL_ARGS="-h /var/tmp/qa_pg -p 55432 -U postgres" \
    python -m unittest tests.test_integration_pg -v

Library stubs in tests/stubs are used only if the real packages are not
installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PSQL_ARGS = os.getenv("QA_TEST_PSQL_ARGS")

for _module in ("telegram", "aiohttp", "httpx", "asyncpg"):
    if importlib.util.find_spec(_module) is None:
        sys.path.insert(0, str(ROOT / "tests" / "stubs"))
        break

DB_NAME = "qa_integration"
UTC = timezone.utc
# Monday 2026-10-05, 11:00 ET
T0 = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
TODAY = date(2026, 10, 5)
OWNER = 111222333


# ---------------------------------------------------------------------------
# psql-backed fake of the asyncpg-facing Database API
# ---------------------------------------------------------------------------
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, D)):
        return f"'{value}'"
    if isinstance(value, (datetime, date)):
        return f"'{value.isoformat()}'"
    if isinstance(value, (list, tuple)):
        items = ",".join(
            '"' + str(v).replace('"', '\\"') + '"' for v in value
        )
        return "'{" + items.replace("'", "''") + "}'"
    if isinstance(value, dict):
        from automation.db import to_json

        return "'" + to_json(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _convert(value):
    if isinstance(value, str):
        if _TS.match(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if _DATE.match(value):
            return date.fromisoformat(value)
    return value


def _psql_db_class():
    from automation.db import Database

    class PsqlDB(_PsqlMixin, Database):
        pass

    return PsqlDB


class _PsqlMixin:
    def __init__(self, dbname: str) -> None:
        self.dbname = dbname
        self.pool = self
        self.queries = 0

    def _run(self, sql: str) -> str:
        cmd = ["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1",
               *shlex.split(PSQL_ARGS), "-d", self.dbname]
        result = subprocess.run(
            cmd, input="SET TIME ZONE 'UTC';\n" + sql, text=True, capture_output=True
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr + "\n" + sql[:500])
        self.queries += 1
        return result.stdout.strip()

    def _render(self, sql: str, args) -> str:
        # Single pass, so text inside inserted literals is never re-scanned.
        sql = re.sub(r"\$(\d+)", lambda m: _literal(args[int(m.group(1)) - 1]), sql)
        return sql.strip().rstrip(";")

    def _rows(self, sql: str, args) -> list[dict]:
        body = self._render(sql, args)
        head = body.lstrip().split(None, 1)[0].upper()
        returns = head in {"SELECT", "WITH"} or re.search(r"\bRETURNING\b", body, re.I)
        if not returns:
            self._run(body + ";")
            return []
        if head in {"SELECT", "WITH"}:
            wrapped = f"SELECT coalesce(json_agg(r), '[]') FROM ({body}) r;"
        else:
            wrapped = f"WITH r AS ({body}) SELECT coalesce(json_agg(r), '[]') FROM r;"
        out = self._run(wrapped)
        data = json.loads(out, parse_float=D) if out else []
        return [{k: _convert(v) for k, v in row.items()} for row in data]

    async def fetch(self, sql, *args):
        return self._rows(sql, args)

    async def fetchrow(self, sql, *args):
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        row = await self.fetchrow(sql, *args)
        return None if row is None else next(iter(row.values()))

    async def execute(self, sql, *args):
        self._rows(sql, args)
        return "OK"

    async def executemany(self, sql, rows):
        for row in rows:
            self._rows(sql, row)

    # pool / connection protocol used by approval._lock_trade
    def acquire(self):
        db = self

        class _Ctx:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    def transaction(self):
        class _Tx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _Tx()


# ---------------------------------------------------------------------------
# Fakes: clock, Telegram bot, UW
# ---------------------------------------------------------------------------
class Clock:
    now = T0


def fake_utcnow():
    return Clock.now


class Msg:
    def __init__(self, message_id, text=None):
        self.message_id = message_id
        self.text_html = text


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.next_id = 1000
        self.fail_next = None

    def _maybe_fail(self):
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self._maybe_fail()
        self.next_id += 1
        self.sent.append(("text", self.next_id, text, reply_markup))
        return Msg(self.next_id, text)

    async def send_photo(self, chat_id, photo, **kwargs):
        self._maybe_fail()
        if getattr(self, "fail_photo_next", None) is not None:
            error, self.fail_photo_next = self.fail_photo_next, None
            raise error
        self.next_id += 1
        self.sent.append(("photo", self.next_id, photo.name, None))
        return Msg(self.next_id)

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None,
                                reply_markup=None):
        self.edits.append((message_id, text))
        return True

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return True

    def texts(self, contains):
        return [s for s in self.sent if s[0] == "text" and contains in s[2]]

    def photos(self, name):
        return [s for s in self.sent if s[0] == "photo" and s[2] == name]


class FakeUW:
    def __init__(self, chain_quotes):
        self.chain_quotes = chain_quotes
        self.current = {}
        self.ticks = []
        self.request_count = 0
        self.rate_limited_count = 0
        self.error_count = 0
        self.last_rate = {}

    async def chain(self, direction, expiry):
        return "SPX", list(self.chain_quotes), {"pages": 1}

    async def quotes_for(self, symbols):
        found = {s: self.current[s] for s in symbols if s in self.current}
        return found, type("R", (), {"latency_ms": 42})()

    async def spot(self, ticker):
        return None

    async def net_prem_ticks(self, ticker):
        return list(self.ticks)

    async def flow_alerts(self, ticker, limit=100):
        return []


def make_quote(symbol, bid, ask, last=None, vol=1500, oi=900, delta="0.30"):
    from automation.models import OptionQuote
    from automation.occ import parse_occ

    return OptionQuote(
        symbol=symbol, occ=parse_occ(symbol), bid=D(bid), ask=D(ask),
        last=D(last) if last else None, volume=vol, open_interest=oi,
        delta=D(delta), implied_volatility=None, last_tape_time=Clock.now,
    )


class FakeQuery:
    def __init__(self, data, user_id, message):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})()
        self.message = message
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        return True


def png_renderers():
    """Real CardRenderers wiring with tiny stand-in drawing functions."""
    from io import BytesIO

    from automation.cards import CardRenderers

    def card(name):
        def make(**kwargs):
            buffer = BytesIO(json.dumps(kwargs).encode())
            buffer.name = name
            return buffer
        return make

    return CardRenderers(
        create_signal_card=card("signal"),
        create_success_card=card("success"),
        create_highest_price_card=card("highest"),
        create_stop_loss_card=card("stop"),
        calculate_highest_result=lambda **k: (0, 0),
        format_strike=lambda v: str(int(D(v))) if D(v) == D(v).to_integral() else v,
        format_premium=lambda v: f"{float(v):.2f}",
    )


# ---------------------------------------------------------------------------
@unittest.skipUnless(PSQL_ARGS, "QA_TEST_PSQL_ARGS not set")
class IntegrationScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", *shlex.split(PSQL_ARGS)]
        subprocess.run(base + ["-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {DB_NAME}"],
                       check=True, capture_output=True)
        subprocess.run(base + ["-d", "postgres", "-c", f"CREATE DATABASE {DB_NAME}"],
                       check=True, capture_output=True)
        for path in sorted((ROOT / "automation" / "migrations").glob("*.sql")):
            subprocess.run(base + ["-d", DB_NAME], input=path.read_text(), text=True,
                           check=True, capture_output=True)

        env = {
            "BOT_TOKEN": "x", "SIGNAL_CHAT_ID": "-100123", "OWNER_USER_ID": str(OWNER),
            "DATABASE_URL": "postgres://unused", "UW_API_KEY": "k", "TV_WEBHOOK_SECRET": "s",
            "TV_INTAKE_ENABLED": "true", "UW_WATCH_ENABLED": "true", "MATCHING_ENABLED": "true",
            "SELECTION_ENABLED": "true", "PROPOSALS_ENABLED": "true", "TRACKING_ENABLED": "true",
            "MILESTONES_ENABLED": "true", "SUCCESS_CARD_ENABLED": "true",
            "HIGHEST_CARD_ENABLED": "true", "HIGHEST_CARD_TRIGGER": "on_close",
            "ENTRY_WINDOW_SECONDS": "0", "UW_WATCH_MIN_NET_PREMIUM": "1000000",
        }
        saved = dict(os.environ)
        os.environ.update(env)
        try:
            from automation import config

            cls.settings = config.load_settings()
        finally:
            os.environ.clear()
            os.environ.update(saved)

        import automation.approval as approval
        import automation.pipeline as pipeline
        import automation.tracker as tracker
        import automation.watch_engine as watch_engine

        for module in (approval, pipeline, tracker, watch_engine):
            module.utcnow = fake_utcnow

    def setUp(self):
        from automation.approval import ApprovalHandler
        from automation.cards import Publisher
        from automation.pipeline import SignalPipeline
        from automation.tracker import Tracker
        from automation.watch_engine import WatchEngine

        self.db = _psql_db_class()(DB_NAME)
        self.bot = FakeBot()
        self.uw = FakeUW(
            [
                make_quote("SPXW261005C07735000", "3.00", "3.90"),
                make_quote("SPXW261005C07740000", "3.30", "3.40"),
                make_quote("SPXW261005C07700000", "5.00", "5.20"),
            ]
        )
        self.publisher = Publisher(self.bot, "-100123", self.db)
        s = self.settings
        self.pipeline = SignalPipeline(s, self.db, self.uw, self.publisher)
        self.tracker = Tracker(s, self.db, self.uw, self.publisher, png_renderers())
        self.watch = WatchEngine(s, self.db, self.uw, self.publisher)

        runtime = type("RT", (), {})()
        runtime.settings = s
        runtime.ready = True
        runtime.db = self.db
        runtime.uw = self.uw
        runtime.publisher = self.publisher
        runtime.tracker = self.tracker
        self.approval = ApprovalHandler(runtime)

    def run_async(self, coro):
        return asyncio.run(coro)

    async def insert_signal(self, signal_id, direction, at):
        from automation import queries as q
        from automation.webhook import parse_signal_payload

        parsed = parse_signal_payload(
            {"direction": direction, "signal_time": at.isoformat(), "ticker": "SPX",
             "timeframe": "1", "signal_id": signal_id, "secret": "s"}
        )
        self.assertNotIn("secret", parsed["payload"])
        return await self.db.fetchval(
            q.INSERT_SIGNAL, parsed["signal_id"], "tradingview", parsed["direction"].value,
            parsed["event_type"], parsed["ticker"], parsed["timeframe"], parsed["tv_price"],
            parsed["signal_time"], parsed["payload"],
        )

    async def click(self, action, proposal_id, user=OWNER, message_id=1):
        query = FakeQuery(f"qa:{action}:{proposal_id}", user, Msg(message_id, "proposal"))
        update = type("Up", (), {"callback_query": query})()
        await self.approval.on_callback(update, None)
        return query

    # -----------------------------------------------------------------------
    def test_full_flow(self):
        self.run_async(self._full_flow())

    async def _full_flow(self):
        from automation import queries as q

        Clock.now = T0
        # UW watch before the indicator (CALL flow)
        self.uw.ticks = [
            {"tape_time": (T0 - timedelta(minutes=1)).isoformat(),
             "net_call_premium": "1500000", "net_put_premium": "-200000"}
        ]
        await self.watch.tick()
        watches = await self.db.fetch("SELECT * FROM uw_watches")
        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0]["direction"], "CALL")
        await self.watch.tick()  # cooldown: no duplicate watch
        self.assertEqual(len(await self.db.fetch("SELECT * FROM uw_watches")), 1)

        # Indicator CALL 10 minutes later -> BOTH_CONFIRMED
        Clock.now = T0 + timedelta(minutes=10)
        signal_db_id = await self.insert_signal("sig-1", "CALL", Clock.now)
        self.assertIsNotNone(signal_db_id)
        self.assertIsNone(await self.insert_signal("sig-1", "CALL", Clock.now))  # duplicate

        self.uw.current["SPXW261005C07740000"] = make_quote("SPXW261005C07740000", "3.30", "3.40")
        await self.pipeline.process(signal_db_id)
        await self.pipeline.process(signal_db_id)  # second claim is a no-op

        signal = await self.db.fetchrow(q.GET_SIGNAL, signal_db_id)
        self.assertEqual(signal["classification"], "BOTH_CONFIRMED")
        self.assertEqual(signal["status"], "PROPOSED")
        match = await self.db.fetchrow("SELECT * FROM signal_matches WHERE signal_db_id = $1",
                                       signal_db_id)
        self.assertEqual(int(match["time_difference_seconds"]), 600)
        self.assertEqual(
            (await self.db.fetchrow("SELECT status FROM uw_watches"))["status"], "MATCHED"
        )
        candidates = await self.db.fetch("SELECT * FROM contract_candidates")
        self.assertEqual(len(candidates), 3)
        proposals = self.bot.texts("TRADE PROPOSAL")
        self.assertEqual(len(proposals), 1)
        self.assertIn("SPXW261005C07740000", proposals[0][2])
        self.assertIn("$4.40", proposals[0][2])  # success level
        proposal = await self.db.fetchrow("SELECT * FROM proposals")
        pid = proposal["id"]

        # Unauthorized click: nothing changes
        query = await self.click("ap", pid, user=999)
        self.assertEqual(query.answers, ["⛔ غير مصرح"])
        self.assertEqual((await self.db.fetchrow(q.GET_PROPOSAL, pid))["status"],
                         "PENDING_APPROVAL")

        # APPROVE: re-quote moved within tolerance (3.40 -> 3.45)
        self.uw.current["SPXW261005C07740000"] = make_quote("SPXW261005C07740000", "3.35", "3.45")
        await self.click("ap", pid)
        await self.click("ap", pid)  # double click -> no second trade
        trades = await self.db.fetch(q.TRACKING_TRADES)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["approved_entry"], D("3.45"))
        self.assertEqual(trade["suggested_entry"], D("3.40"))
        self.assertEqual(len(self.bot.photos("signal")), 1)
        self.assertIsNotNone(trade["tracking_message_id"])

        # Tracking: +10% (3.80 >= 3.795)
        async def poll(bid, ask):
            self.uw.current["SPXW261005C07740000"] = make_quote("SPXW261005C07740000", bid, ask)
            Clock.now += timedelta(seconds=15)
            await self.tracker.tick()

        await poll("3.80", "3.90")
        self.assertEqual(len(self.bot.texts("| +10% |")), 1)
        # jump to +30% and SUCCESS (3.45 + 1.00 = 4.45); 4.50 = +30.4%
        mark = len(self.bot.sent)
        await poll("4.50", "4.60")
        # +20% (4.14), SUCCESS (4.45), +30% (4.485): each published, in order
        burst = [(kind, name if kind == "photo" else text.split("|")[1].strip())
                 for kind, _, name_or_text, _ in self.bot.sent[mark:]
                 for name, text in [(name_or_text, name_or_text)]]
        self.assertEqual(burst, [("text", "+20%"), ("photo", "success"), ("text", "+30%")])
        self.assertIn("Bid $4.50", self.bot.texts("| +20% |")[0][2])  # detecting bid
        # drop and re-cross: no repeats
        await poll("3.60", "3.70")
        await poll("4.55", "4.65")
        self.assertEqual(len(self.bot.texts("| +30% |")), 1)
        self.assertEqual(len(self.bot.photos("success")), 1)
        # continue after success: +40%
        await poll("4.85", "4.95")
        self.assertEqual(len(self.bot.texts("| +40% |")), 1)

        trade = await self.db.fetchrow(q.GET_TRADE, trade["id"])
        self.assertEqual(trade["highest_bid"], D("4.85"))
        self.assertEqual(trade["highest_milestone_sent"], 40)
        self.assertTrue(trade["success_sent"])
        self.assertEqual(trade["lowest_bid"], D("3.60"))
        last_edit = self.bot.edits[-1][1]
        self.assertIn("⭐ +$100 SUCCESS", last_edit)
        self.assertLess(last_edit.index("+20%"), last_edit.index("SUCCESS"))
        self.assertLess(last_edit.index("SUCCESS"), last_edit.index("+30%"))

        # "Restart": brand-new tracker + publisher, same DB -> nothing re-sent
        from automation.cards import Publisher
        from automation.tracker import Tracker

        sent_before = len(self.bot.sent)
        restarted = Tracker(self.settings, self.db, self.uw,
                            Publisher(self.bot, "-100123", self.db), png_renderers())
        Clock.now += timedelta(seconds=15)
        await restarted.tick()
        self.assertEqual(len(self.bot.sent), sent_before)

        # Session end closes the trade and (trigger on_close) sends highest card once
        Clock.now = datetime(2026, 10, 5, 20, 0, 30, tzinfo=UTC)  # 16:00:30 ET
        await restarted.tick()
        await restarted.tick()
        self.assertEqual(len(self.bot.photos("highest")), 1)
        closed = await self.db.fetchrow(q.GET_TRADE, trade["id"])
        self.assertEqual(closed["status"], "CLOSED")
        self.assertEqual(closed["close_reason"], "SESSION_END")

        # Ledger is unique per event
        events = await self.db.fetch(
            "SELECT event_type, level, status FROM events WHERE entity_type = 'trade'"
        )
        kinds = sorted((e["event_type"], e["level"]) for e in events)
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertIn(("MILESTONE", 20), kinds)

    # -----------------------------------------------------------------------
    def test_reject_reconfirm_cancel_conflict_uw_only(self):
        self.run_async(self._other_paths())

    async def _other_paths(self):
        from automation import queries as q
        from telegram.error import TimedOut

        base = T0 + timedelta(days=1)  # Tuesday 2026-10-06
        Clock.now = base
        self.uw.chain_quotes = [make_quote("SPXW261006P07690000", "3.30", "3.40")]
        self.uw.current["SPXW261006P07690000"] = make_quote("SPXW261006P07690000", "3.30", "3.40")

        # PUT watch, then indicator CALL -> CONFLICT
        self.uw.ticks = [{"tape_time": (base - timedelta(minutes=1)).isoformat(),
                          "net_call_premium": "-100000", "net_put_premium": "2000000"}]
        await self.watch.tick()
        Clock.now = base + timedelta(minutes=5)
        self.uw.chain_quotes = [make_quote("SPXW261006C07735000", "3.30", "3.40")]
        sid = await self.insert_signal("sig-conflict", "CALL", Clock.now)
        await self.pipeline.process(sid)
        self.assertEqual((await self.db.fetchrow(q.GET_SIGNAL, sid))["classification"], "CONFLICT")
        p1 = await self.db.fetchrow("SELECT * FROM proposals WHERE signal_db_id = $1", sid)

        # REJECT
        await self.click("rj", p1["id"])
        self.assertEqual((await self.db.fetchrow(q.GET_PROPOSAL, p1["id"]))["status"], "REJECTED")
        query = await self.click("ap", p1["id"])  # approve after reject -> refused
        self.assertIn("REJECTED", query.answers[-1])

        # New signal (INDICATOR_ONLY, no watch in window) -> price jumps -> RECONFIRM
        Clock.now = base + timedelta(hours=2)
        self.uw.chain_quotes = [make_quote("SPXW261006C07740000", "3.30", "3.40")]
        sid2 = await self.insert_signal("sig-2", "CALL", Clock.now)
        await self.pipeline.process(sid2)
        self.assertEqual((await self.db.fetchrow(q.GET_SIGNAL, sid2))["classification"],
                         "INDICATOR_ONLY")
        p2 = await self.db.fetchrow("SELECT * FROM proposals WHERE signal_db_id = $1", sid2)
        self.uw.current["SPXW261006C07740000"] = make_quote("SPXW261006C07740000", "3.55", "3.65")
        await self.click("ap", p2["id"])
        row = await self.db.fetchrow(q.GET_PROPOSAL, p2["id"])
        self.assertEqual(row["status"], "NEEDS_RECONFIRM")
        self.assertEqual(row["reconfirm_price"], D("3.65"))
        self.assertEqual(len(self.bot.texts("RECONFIRM")), 1)
        # CANCEL
        await self.click("cn", p2["id"])
        self.assertEqual((await self.db.fetchrow(q.GET_PROPOSAL, p2["id"]))["status"], "CANCELLED")
        self.assertEqual(len(await self.db.fetch(
            "SELECT * FROM trades WHERE proposal_id = $1", p2["id"])), 0)

        # RECONFIRM path that succeeds
        Clock.now = base + timedelta(hours=3)
        self.uw.chain_quotes = [make_quote("SPXW261006C07750000", "3.30", "3.40")]
        sid3 = await self.insert_signal("sig-3", "CALL", Clock.now)
        await self.pipeline.process(sid3)
        p3 = await self.db.fetchrow("SELECT * FROM proposals WHERE signal_db_id = $1", sid3)
        self.uw.current["SPXW261006C07750000"] = make_quote("SPXW261006C07750000", "3.55", "3.65")
        await self.click("ap", p3["id"])
        await self.click("rc", p3["id"])
        trade = await self.db.fetchrow("SELECT * FROM trades WHERE proposal_id = $1", p3["id"])
        self.assertEqual(trade["approved_entry"], D("3.65"))

        # Jump +10%/+20%/SUCCESS/+30% (entry 3.65) where the SUCCESS card
        # fails definitively (FAILED): later events wait; after a "restart",
        # with a lower price, SUCCESS is retried and then +30% follows.
        from automation.cards import Publisher
        from automation.tracker import Tracker
        from telegram.error import BadRequest

        def labels(start):
            return [(k, n if k == "photo" else n.split("|")[1].strip())
                    for k, _, n, _ in self.bot.sent[start:]]

        self.bot.fail_photo_next = BadRequest("boom")
        mark = len(self.bot.sent)
        self.uw.current["SPXW261006C07750000"] = make_quote("SPXW261006C07750000", "4.80", "4.90")
        Clock.now += timedelta(seconds=15)
        await self.tracker.tick()
        self.assertEqual(labels(mark), [("text", "+10%"), ("text", "+20%")])
        row = await self.db.fetchrow(q.GET_TRADE, trade["id"])
        self.assertEqual(row["highest_milestone_sent"], 20)
        self.assertFalse(row["success_sent"])

        restarted = Tracker(self.settings, self.db, self.uw,
                            Publisher(self.bot, "-100123", self.db), png_renderers())
        self.uw.current["SPXW261006C07750000"] = make_quote("SPXW261006C07750000", "4.00", "4.10")
        Clock.now += timedelta(seconds=15)
        await restarted.tick()
        Clock.now += timedelta(seconds=15)
        await restarted.tick()
        self.assertEqual(labels(mark), [("text", "+10%"), ("text", "+20%"),
                                        ("photo", "success"), ("text", "+30%")])
        row = await self.db.fetchrow(q.GET_TRADE, trade["id"])
        self.assertTrue(row["success_sent"])
        self.assertEqual(row["highest_milestone_sent"], 30)

        # Telegram timeout on +40% -> UNCERTAIN: never resent, never treated
        # as SENT, and +50% after it stays blocked.
        mark = len(self.bot.sent)
        self.bot.fail_next = TimedOut("timeout")
        self.uw.current["SPXW261006C07750000"] = make_quote("SPXW261006C07750000", "5.20", "5.30")
        Clock.now += timedelta(seconds=15)
        await restarted.tick()
        self.uw.current["SPXW261006C07750000"] = make_quote("SPXW261006C07750000", "5.60", "5.70")
        Clock.now += timedelta(seconds=15)
        await restarted.tick()
        status = await self.db.fetchrow(
            "SELECT status FROM events WHERE entity_type='trade' AND entity_id=$1 "
            "AND event_type='MILESTONE' AND level=40", trade["id"])
        self.assertEqual(status["status"], "UNCERTAIN")
        self.assertEqual(len(self.bot.texts("| +40% |")), 0)
        self.assertEqual(len(self.bot.texts("| +50% |")), 0)
        self.assertEqual(len(self.bot.texts("تعذر التأكد")), 1)
        row = await self.db.fetchrow(q.GET_TRADE, trade["id"])
        self.assertEqual(row["highest_milestone_sent"], 30)

        # Stale PENDING (crash between claim and mark) -> real SQL sweep moves
        # it to NEEDS_RECONCILIATION (no resend); owner 'resend' -> FAILED.
        await self.db.execute(
            "INSERT INTO events (entity_type, entity_id, event_type, level, status, "
            "attempts, updated_at) VALUES ('trade', $1, 'MILESTONE', 90, 'PENDING', 1, "
            "now() - interval '20 minutes')", trade["id"])
        await self.db.execute(
            "INSERT INTO events (entity_type, entity_id, event_type, level, status, "
            "attempts) VALUES ('trade', $1, 'MILESTONE', 100, 'PENDING', 1)", trade["id"])
        stale = await restarted.recover_stale_pending()
        self.assertEqual([(r["event_type"], r["level"]) for r in stale], [("MILESTONE", 90)])
        self.assertEqual(len(self.bot.texts("/qa_reconcile")), 1)
        fresh = await self.db.fetchval(
            "SELECT status FROM events WHERE entity_id=$1 AND level=100", trade["id"])
        self.assertEqual(fresh, "PENDING")  # recent PENDING is left alone
        self.assertEqual(await restarted.recover_stale_pending(), [])  # alert once
        reconciled = await restarted.reconcile_event(stale[0]["id"], "resend")
        self.assertEqual(reconciled["status"], "FAILED")
        self.assertIsNone(await restarted.reconcile_event(stale[0]["id"], "sent"))

        # Watch expiry -> UW_ONLY (a CALL watch with no indicator signal)
        Clock.now = base + timedelta(hours=4)
        self.uw.ticks = [{"tape_time": (Clock.now - timedelta(minutes=1)).isoformat(),
                          "net_call_premium": "3000000", "net_put_premium": "0"}]
        await self.watch.tick()
        await self.db.execute(
            "UPDATE uw_watches SET expires_at = now() - interval '2 hours' "
            "WHERE status = 'INDICATOR_PENDING'")
        await self.watch.expire_due()
        uw_only = await self.db.fetch(
            "SELECT * FROM signal_matches WHERE classification = 'UW_ONLY'")
        self.assertEqual(len(uw_only), 1)
        # No proposal was ever created from a watch alone
        self.assertEqual(
            await self.db.fetchval("SELECT count(*) FROM proposals WHERE signal_db_id IS NULL"), 0)


if __name__ == "__main__":
    unittest.main()

"""Acceptance tests: STALE PENDING recovery (crash between claim and mark).

A  crash BEFORE Telegram delivery -> event kept, never SENT, later events
   blocked; after EVENT_PENDING_STALE_SECONDS -> NEEDS_RECONCILIATION +
   one owner alert; owner 'resend' -> sent once, queue continues in order.
B  crash AFTER Telegram accepted the message, BEFORE mark SENT -> no
   automatic resend (no blind duplicate); owner 'sent' -> queue continues,
   the message is never sent twice.
C  ordering: +10 SENT, +20 stale PENDING -> no SUCCESS, no +30,
   highest_milestone_sent stays at the last SENT event.
D  definite FAILED still retries -> SENT -> rest in order.
E  SENT events are never repeated after a restart.

Uses the in-memory fake from tests/test_delivery_success_delta.py.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta

from tests.test_delivery_success_delta import (  # noqa: E402
    BadRequest,
    Clock,
    FakeBot,
    Harness,
    MemoryDB,
    q,
)


class SimulatedCrash(BaseException):
    """Process death: not an Exception, so nothing in the app catches it."""


class StaleAwareDB(MemoryDB):
    """MemoryDB + updated_at tracking + the two new statements."""

    async def _dispatch(self, sql, args):
        if sql is q.MARK_STALE_PENDING:
            rows = []
            for (etype, eid, kind, level), event in self.events.items():
                age = (Clock.now - event["updated_at"]).total_seconds()
                if event["status"] == "PENDING" and age > args[0]:
                    event.update(status="NEEDS_RECONCILIATION", updated_at=Clock.now)
                    rows.append({"id": event["id"], "entity_type": etype,
                                 "entity_id": eid, "event_type": kind, "level": level})
            return rows
        if sql is q.RECONCILE_EVENT:
            for (etype, eid, kind, level), event in self.events.items():
                if event["id"] == args[0] and event["status"] == "NEEDS_RECONCILIATION":
                    event.update(status=args[1], updated_at=Clock.now)
                    return [{"id": event["id"], "entity_type": etype, "entity_id": eid,
                             "event_type": kind, "level": level, "status": args[1]}]
            return []
        result = await super()._dispatch(sql, args)
        if sql in (q.CLAIM_EVENT, q.RECLAIM_FAILED_EVENT, q.MARK_EVENT):
            for event in self.events.values():
                event.setdefault("updated_at", Clock.now)
            if result and sql is not q.MARK_EVENT:
                for event in self.events.values():
                    if event["id"] == result[0]["id"]:
                        event["updated_at"] = Clock.now
            if sql is q.MARK_EVENT:
                for event in self.events.values():
                    if event["id"] == args[0]:
                        event["updated_at"] = Clock.now
        return result


class CrashBot(FakeBot):
    """crash_before: die before Telegram gets the message.
    crash_after: Telegram accepts the message, then the process dies."""

    def __init__(self):
        super().__init__()
        self.crash_before = set()
        self.crash_after = set()

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        label = text.split("|")[1].strip() if "|" in text else text
        if label in self.crash_before:
            self.crash_before.discard(label)
            raise SimulatedCrash()
        message = await super().send_message(chat_id, text, parse_mode, reply_markup)
        if label in self.crash_after:
            self.crash_after.discard(label)
            raise SimulatedCrash()
        return message


def run(coro):
    return asyncio.run(coro)


def queue(bot):
    return [x for x in bot.log if not x.startswith("⚠️")]


def alerts(bot):
    return [x for x in bot.log if x.startswith("⚠️")]


async def crash_poll(test, harness, bid):
    with test.assertRaises(SimulatedCrash):
        await harness.poll(bid)


async def tick(harness):
    """One leader tick: stale sweep, then the normal poll."""
    await harness.tracker.recover_stale_pending()


class StalePendingTests(unittest.TestCase):
    def setUp(self):
        self.db = StaleAwareDB()
        self.db.add_trade("4.00")
        self.bot = CrashBot()

    def harness(self):
        return Harness(self.db, self.bot, success_card=True)

    # -------------------------------------------------------------- A + C
    def test_a_crash_before_delivery_then_owner_resend(self):
        async def scenario():
            first = self.harness()
            await first.poll("4.20")
            self.bot.crash_before.add("+20%")
            await crash_poll(self, first, "5.30")  # +10 SENT, +20 claimed, crash

            self.assertEqual(self.db.status("MILESTONE", 10), "SENT")
            self.assertEqual(self.db.status("MILESTONE", 20), "PENDING")
            self.assertEqual(queue(self.bot), ["+10%"])

            # Restart inside the stale window: nothing resent, nothing skipped.
            restarted = self.harness()
            await tick(restarted)
            await restarted.poll("5.30")
            self.assertEqual(self.db.status("MILESTONE", 20), "PENDING")
            self.assertIsNone(self.db.status("SUCCESS_CARD"))          # C
            self.assertIsNone(self.db.status("MILESTONE", 30))         # C
            self.assertEqual(self.db.trades[1]["highest_milestone_sent"], 10)  # C
            self.assertFalse(self.db.trades[1]["success_sent"])
            self.assertEqual(queue(self.bot), ["+10%"])

            # Past EVENT_PENDING_STALE_SECONDS -> NEEDS_RECONCILIATION, one alert.
            Clock.now += timedelta(seconds=601)
            await tick(restarted)
            await restarted.poll("5.30")
            await tick(restarted)
            self.assertEqual(self.db.status("MILESTONE", 20), "NEEDS_RECONCILIATION")
            self.assertEqual(len(alerts(self.bot)), 1)
            self.assertIn("/qa_reconcile", alerts(self.bot)[0])
            self.assertEqual(queue(self.bot), ["+10%"])                 # no blind resend
            self.assertIsNone(self.db.status("SUCCESS_CARD"))

            # Owner: it never appeared -> resend. Sent once, then the rest in order.
            event_id = self.db.events[("trade", 1, "MILESTONE", 20)]["id"]
            row = await restarted.tracker.reconcile_event(event_id, "resend")
            self.assertEqual(row["status"], "FAILED")
            await restarted.poll("4.50")
            self.assertEqual(queue(self.bot), ["+10%", "+20%", "SUCCESS_CARD", "+30%"])
            self.assertEqual(self.db.trades[1]["highest_milestone_sent"], 30)
            # A second reconcile on the same event is refused.
            self.assertIsNone(await restarted.tracker.reconcile_event(event_id, "sent"))

        run(scenario())

    # ------------------------------------------------------------------ B + E
    def test_b_crash_after_telegram_accepted_no_blind_duplicate(self):
        async def scenario():
            first = self.harness()
            await first.poll("4.20")
            self.bot.crash_after.add("+20%")
            await crash_poll(self, first, "5.30")  # Telegram HAS +20, DB says PENDING
            self.assertEqual(queue(self.bot), ["+10%", "+20%"])
            self.assertEqual(self.db.status("MILESTONE", 20), "PENDING")

            restarted = self.harness()
            for _ in range(3):  # restarts / polls inside and after the stale window
                await tick(restarted)
                await restarted.poll("5.30")
                Clock.now += timedelta(seconds=300)
            await tick(restarted)
            self.assertEqual(self.db.status("MILESTONE", 20), "NEEDS_RECONCILIATION")
            self.assertEqual(queue(self.bot).count("+20%"), 1)          # never resent
            self.assertIsNone(self.db.status("SUCCESS_CARD"))

            # Owner sees +20% in the channel -> 'sent'. Queue continues, no duplicate.
            event_id = self.db.events[("trade", 1, "MILESTONE", 20)]["id"]
            await restarted.tracker.reconcile_event(event_id, "sent")
            again = self.harness()  # E: another restart
            await again.poll("4.50")
            await again.poll("4.50")
            self.assertEqual(queue(self.bot), ["+10%", "+20%", "SUCCESS_CARD", "+30%"])
            self.assertEqual(self.db.trades[1]["highest_milestone_sent"], 30)

        run(scenario())

    # ---------------------------------------------------------------------- D
    def test_d_definite_failure_still_retries_in_order(self):
        async def scenario():
            h = self.harness()
            self.bot.fail_rules.append(["+20%", BadRequest("down"), 1])
            await h.poll("4.20")
            await h.poll("5.30")
            self.assertEqual(self.db.status("MILESTONE", 20), "FAILED")
            self.assertEqual(queue(self.bot), ["+10%"])
            await tick(h)  # the stale sweep never touches FAILED
            self.assertEqual(self.db.status("MILESTONE", 20), "FAILED")
            await h.poll("4.50")
            self.assertEqual(queue(self.bot), ["+10%", "+20%", "SUCCESS_CARD", "+30%"])
            self.assertEqual(alerts(self.bot), [])

        run(scenario())


if __name__ == "__main__":
    unittest.main()

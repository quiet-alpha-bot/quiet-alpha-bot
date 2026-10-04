"""Unit tests for the pure decision logic (stdlib unittest, no network/DB).

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

from automation import config as cfg
from automation.entry import EntryObservation, decide_entry, within_tolerance
from automation.matching import WatchRef, classify_signal, late_observation
from automation.milestones import (
    TrackState,
    apply_observation,
    is_success,
    milestone_level,
    new_milestones,
    pending_events,
    progress_items,
    success_price,
)
from automation.models import (
    Classification,
    Direction,
    EntryDecision,
    OptionQuote,
    parse_ts,
    quote_from_uw_row,
)
from automation.occ import parse_occ, strike_text
from automation.selection import select_contract
from automation.watch_logic import evaluate_flow, watch_key

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # 11:00 ET


def settings():
    # Fresh defaults, independent of the developer's environment.
    saved = dict(os.environ)
    try:
        for key in list(os.environ):
            if key.isupper():
                os.environ.pop(key)
        return cfg.load_settings()
    finally:
        os.environ.clear()
        os.environ.update(saved)


S = settings()


def q(symbol, bid, ask, last=None, vol=1000, oi=1000, delta="0.30", tape=NOW):
    return OptionQuote(
        symbol=symbol,
        occ=parse_occ(symbol),
        bid=None if bid is None else D(bid),
        ask=None if ask is None else D(ask),
        last=None if last is None else D(last),
        volume=vol,
        open_interest=oi,
        delta=None if delta is None else D(delta),
        implied_volatility=None,
        last_tape_time=tape,
    )


class OccTests(unittest.TestCase):
    def test_parse_roundtrip(self):
        occ = parse_occ("SPXW261005C07735000")
        self.assertEqual(occ.root, "SPXW")
        self.assertEqual(occ.expiry, date(2026, 10, 5))
        self.assertEqual(occ.right, "CALL")
        self.assertEqual(occ.strike, D(7735))
        self.assertEqual(occ.symbol, "SPXW261005C07735000")
        self.assertEqual(strike_text(occ.strike), "7735")

    def test_half_strike_and_invalid(self):
        occ = parse_occ("SPXW261005P05812500")
        self.assertEqual(strike_text(occ.strike), "5812.5")
        self.assertIsNone(parse_occ("garbage"))
        self.assertIsNone(parse_occ(None))

    def test_uw_row(self):
        row = {
            "option_symbol": "SPXW261005P07690000",
            "nbbo_bid": "3.30",
            "nbbo_ask": "3.40",
            "last_price": "3.35",
            "volume": 1200,
            "open_interest": "800",
            "delta": "-0.31",
            "last_tape_time": "2026-10-05T15:00:00.000Z",
        }
        quote = quote_from_uw_row(row)
        self.assertEqual(quote.bid, D("3.30"))
        self.assertEqual(quote.spread, D("0.10"))
        self.assertEqual(quote.open_interest, 800)
        self.assertEqual(quote.last_tape_time, NOW)


class MatchingTests(unittest.TestCase):
    window = timedelta(minutes=30)

    def w(self, i, direction, minutes_before):
        return WatchRef(i, direction, NOW - timedelta(minutes=minutes_before))

    def test_indicator_only(self):
        r = classify_signal(Direction.CALL, NOW, [], self.window)
        self.assertEqual(r.classification, Classification.INDICATOR_ONLY)

    def test_both_confirmed_and_time_difference(self):
        r = classify_signal(
            Direction.CALL, NOW, [self.w(1, Direction.CALL, 12)], self.window
        )
        self.assertEqual(r.classification, Classification.BOTH_CONFIRMED)
        self.assertEqual(r.time_difference_seconds(NOW), 720)

    def test_conflict(self):
        r = classify_signal(
            Direction.PUT, NOW, [self.w(1, Direction.CALL, 5)], self.window
        )
        self.assertEqual(r.classification, Classification.CONFLICT)

    def test_mixed_policies(self):
        watches = [self.w(1, Direction.CALL, 20), self.w(2, Direction.PUT, 5)]
        r = classify_signal(Direction.CALL, NOW, watches, self.window, "CONFLICT")
        self.assertEqual(r.classification, Classification.CONFLICT)
        r = classify_signal(Direction.CALL, NOW, watches, self.window, "LATEST")
        self.assertEqual(r.classification, Classification.CONFLICT)
        r = classify_signal(Direction.PUT, NOW, watches, self.window, "LATEST")
        self.assertEqual(r.classification, Classification.BOTH_CONFIRMED)

    def test_no_lookahead_and_window(self):
        future = WatchRef(1, Direction.CALL, NOW + timedelta(seconds=1))
        too_old = self.w(2, Direction.CALL, 31)
        r = classify_signal(Direction.CALL, NOW, [future, too_old], self.window)
        self.assertEqual(r.classification, Classification.INDICATOR_ONLY)

    def test_late_observation(self):
        self.assertEqual(
            late_observation(Direction.CALL, Direction.CALL),
            Classification.LATE_UW_AGREEMENT,
        )
        self.assertEqual(
            late_observation(Direction.PUT, Direction.CALL),
            Classification.LATE_UW_DISAGREEMENT,
        )


class WatchLogicTests(unittest.TestCase):
    def tick(self, minutes_before, call, put):
        return {
            "tape_time": (NOW - timedelta(minutes=minutes_before)).isoformat(),
            "net_call_premium": str(call),
            "net_put_premium": str(put),
            "call_volume": 10,
        }

    def test_bullish_trigger(self):
        ticks = [self.tick(1, 900000, -100000), self.tick(2, 500000, 0)]
        ev = evaluate_flow(ticks, NOW, 5, D(1000000), D("0.3"))
        self.assertEqual(ev.direction, Direction.CALL)
        self.assertTrue(ev.triggered)
        self.assertEqual(ev.directional_premium, D(1500000))

    def test_outside_window_ignored_and_no_future(self):
        ticks = [self.tick(10, 9e9, 0), self.tick(-1, 9e9, 0), self.tick(1, 10, 0)]
        ev = evaluate_flow(ticks, NOW, 5, D(1000000), D("0.3"))
        self.assertEqual(ev.ticks_used, 1)
        self.assertFalse(ev.triggered)

    def test_bearish(self):
        ticks = [self.tick(1, -200000, 2000000)]
        ev = evaluate_flow(ticks, NOW, 5, D(1000000), D("0.3"))
        self.assertEqual(ev.direction, Direction.PUT)
        self.assertTrue(ev.triggered)

    def test_watch_key_bucket(self):
        k1 = watch_key(Direction.CALL, NOW, 15)
        k2 = watch_key(Direction.CALL, NOW + timedelta(minutes=1), 15)
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, watch_key(Direction.PUT, NOW, 15))


class SelectionTests(unittest.TestCase):
    def test_hard_filters_and_spread_ranking(self):
        quotes = [
            q("SPXW261005C07735000", "3.00", "3.90"),  # wide
            q("SPXW261005C07740000", "3.30", "3.50"),  # tight
            q("SPXW261005C07700000", "5.00", "5.20"),  # out of range
            q("SPXW261005P07735000", "3.30", "3.40"),  # wrong type
            q("SPX261005C07735000", "3.30", "3.40"),  # wrong root
            q("SPXW261006C07735000", "3.30", "3.40"),  # wrong expiry
            q("SPXW261005C07750000", "0", "3.00"),  # zero bid
        ]
        result = select_contract(
            quotes, Direction.CALL, date(2026, 10, 5), "SPXW", S.selection, NOW
        )
        self.assertEqual(result.selected.quote.symbol, "SPXW261005C07740000")
        reasons = {c.quote.symbol: c.reject_reasons for c in result.candidates}
        self.assertIn("PREMIUM_OUT_OF_RANGE", reasons["SPXW261005C07700000"])
        self.assertIn("TYPE_MISMATCH", reasons["SPXW261005P07735000"])
        self.assertIn("ROOT_MISMATCH", reasons["SPX261005C07735000"])
        self.assertIn("EXPIRY_MISMATCH", reasons["SPXW261006C07735000"])
        self.assertIn("ZERO_BID", reasons["SPXW261005C07750000"])
        self.assertEqual(len(result.passed), 2)

    def test_no_contract(self):
        result = select_contract(
            [q("SPXW261005C07700000", "6", "6.2")],
            Direction.CALL,
            date(2026, 10, 5),
            "SPXW",
            S.selection,
            NOW,
        )
        self.assertIsNone(result.selected)


class EntryTests(unittest.TestCase):
    def obs(self, seconds, bid, ask):
        return EntryObservation(NOW + timedelta(seconds=seconds), D(bid), D(ask))

    def decide(self, history, now_offset, first_ask="3.40", signal_age=10):
        return decide_entry(
            history,
            D(first_ask),
            NOW - timedelta(seconds=signal_age),
            NOW,
            NOW + timedelta(seconds=now_offset),
            S.entry,
            S.selection,
        )

    def test_enter_when_tight_and_stable(self):
        r = self.decide([self.obs(0, "3.35", "3.40")], 0)
        self.assertEqual(r.decision, EntryDecision.ENTER)
        self.assertEqual(r.price, D("3.40"))

    def test_wait_then_window_end(self):
        wide = [self.obs(0, "3.00", "3.40")]
        self.assertEqual(self.decide(wide, 5).decision, EntryDecision.WAIT)
        self.assertEqual(self.decide(wide, 31).decision, EntryDecision.ENTER)

    def test_chase_and_age_and_time(self):
        self.assertEqual(
            self.decide([self.obs(0, "3.90", "3.95")], 0).reason, "CHASE_LIMIT"
        )
        self.assertEqual(
            self.decide([self.obs(0, "3.35", "3.40")], 0, signal_age=999).reason,
            "SIGNAL_TOO_OLD",
        )
        late = datetime(2026, 10, 5, 19, 50, tzinfo=UTC)  # 15:50 ET
        r = decide_entry(
            [EntryObservation(late, D("3.35"), D("3.40"))],
            D("3.40"),
            late,
            late,
            late,
            S.entry,
            S.selection,
        )
        self.assertEqual(r.reason, "PAST_LAST_ENTRY_TIME")

    def test_tolerance(self):
        self.assertTrue(within_tolerance(D("3.40"), D("3.50"), D("0.10"), None))
        self.assertFalse(within_tolerance(D("3.40"), D("3.55"), D("0.10"), None))
        self.assertFalse(within_tolerance(D("3.40"), D("3.41"), None, None))
        self.assertTrue(within_tolerance(D("3.40"), D("3.40"), None, None))


class MilestoneTests(unittest.TestCase):
    def test_levels_from_spec(self):
        entry = D("4.00")
        self.assertEqual(milestone_level(entry, D("4.40"), 10), 10)
        self.assertEqual(milestone_level(entry, D("4.79"), 10), 10)
        self.assertEqual(milestone_level(entry, D("5.60"), 10), 40)
        self.assertEqual(milestone_level(entry, D("3.90"), 10), 0)

    def test_no_repeat_after_drop(self):
        self.assertEqual(new_milestones(0, 30, 10), [10, 20, 30])
        self.assertEqual(new_milestones(30, 30, 10), [])
        self.assertEqual(new_milestones(30, 20, 10), [])
        self.assertEqual(new_milestones(30, 40, 10), [40])

    def test_success_is_entry_plus_one_dollar(self):
        gain = D("1.00")
        for entry, target in [("2.50", "3.50"), ("3.40", "4.40"), ("4.00", "5.00")]:
            self.assertEqual(success_price(D(entry), gain), D(target))
        self.assertTrue(is_success(D("4.00"), D("5.00"), gain))
        self.assertFalse(is_success(D("4.00"), D("4.99"), gain))

    def test_progress_order_success_between_levels(self):
        items = progress_items(D("4.00"), 40, 10, True, D("1.00"))
        self.assertEqual(
            [label for _, label in items],
            ["+10%", "+20%", "SUCCESS", "+30%", "+40%"],
        )

    def test_jump_publishes_every_level_and_success_in_price_order(self):
        # Spec example: entry 4.00, Observed Bid jumps 4.20 -> 5.30 in one poll.
        entry = D("4.00")
        reached = milestone_level(entry, D("5.30"), 10)
        newly = new_milestones(0, reached, 10)
        detections = {level: D("5.30") for level in newly}
        events = pending_events(entry, 10, 0, reached, detections, True, D("5.30"), D("1.00"))
        self.assertEqual(
            [(e.kind, e.level) for e in events],
            [("MILESTONE", 10), ("MILESTONE", 20), ("SUCCESS", 0), ("MILESTONE", 30)],
        )
        self.assertEqual([e.threshold for e in events],
                         [D("4.40"), D("4.80"), D("5.00"), D("5.20")])
        # Bid shown = the detecting Observed Bid, not a synthetic threshold price.
        self.assertTrue(all(e.bid == D("5.30") for e in events))

    def test_pending_resumes_after_partial_publish(self):
        entry = D("4.00")
        detections = {10: D("5.30"), 20: D("5.30"), 30: D("5.30")}
        # +10 and +20 already sent, success not yet: success then +30.
        events = pending_events(entry, 10, 20, 30, detections, True, D("5.30"), D("1.00"))
        self.assertEqual([(e.kind, e.level) for e in events],
                         [("SUCCESS", 0), ("MILESTONE", 30)])
        # Everything sent: nothing pending even if price fell.
        self.assertEqual(pending_events(entry, 10, 30, 30, detections, False, None, D("1")), [])

    def test_tie_success_before_milestone(self):
        # entry 2.50: SUCCESS 3.50 == +40% exactly -> SUCCESS_CARD first.
        events = pending_events(D("2.50"), 10, 30, 40, {40: D("3.50")}, True, D("3.50"), D("1.00"))
        self.assertEqual([(e.kind, e.level) for e in events],
                         [("SUCCESS", 0), ("MILESTONE", 40)])

    def test_apply_observation(self):
        state = TrackState(*([None] * 9))
        state, high = apply_observation(state, D("3.5"), D("3.6"), D("3.55"), NOW)
        self.assertTrue(high)
        state, high = apply_observation(state, D("3.2"), D("3.3"), None, NOW)
        self.assertFalse(high)
        self.assertEqual(state.highest_bid, D("3.5"))
        self.assertEqual(state.lowest_bid, D("3.2"))
        self.assertEqual(state.highest_mid, D("3.55"))


class ConfigTests(unittest.TestCase):
    def test_owner_id_parsing(self):
        self.assertEqual(cfg.parse_owner_user_id("12345"), 12345)
        self.assertIsNone(cfg.parse_owner_user_id("-100123"))
        self.assertIsNone(cfg.parse_owner_user_id("abc"))
        self.assertIsNone(cfg.parse_owner_user_id(None))
        self.assertIsNone(cfg.parse_owner_user_id("0"))

    def test_defaults_are_off(self):
        flags = S.flags
        self.assertFalse(any(vars(flags).values()))
        self.assertIn("OWNER_USER_ID is missing or invalid "
                      "(must be a positive Telegram user id)", S.problems())
        self.assertNotIn("secret", repr(S).lower())

    def test_parse_ts(self):
        self.assertEqual(parse_ts(1791212400000), parse_ts(1791212400))
        self.assertEqual(parse_ts("2026-10-05T15:00:00Z"), NOW)


if __name__ == "__main__":
    unittest.main()

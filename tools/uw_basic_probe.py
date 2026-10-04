#!/usr/bin/env python3
"""UW Basic probe — measure what Unusual Whales REST really delivers.

Standalone and read-only:
  * no Telegram, no database, no orders
  * UW_API_KEY is read from the environment only and is never printed,
    logged or written to any file
  * standard library only (no installs needed)

Usage (during the SPX session):

  export UW_API_KEY=...            # never paste it into code
  python tools/uw_basic_probe.py headers
  python tools/uw_basic_probe.py chain --direction call
  python tools/uw_basic_probe.py flow
  python tools/uw_basic_probe.py track --symbol SPXW261005C07735000 \
         --interval 5 --duration 900 --entry 3.40 --out track.csv
  python tools/uw_basic_probe.py burst --n 20 --interval 0.5 --yes

What it reports:
  rate-limit headers, endpoint latency, Bid/Ask behaviour, how often the
  quote changes between polls, unchanged (stale) streaks, last_tape_time
  age, request usage, 429 and timeout behaviour, and — with --entry —
  whether +10% milestones and Entry+$1.00 were observable at that interval.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

BASE_URL = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com")
RATE_HEADERS = (
    "x-uw-daily-req-count",
    "x-uw-token-req-limit",
    "x-uw-minute-req-counter",
    "x-uw-req-per-minute-remaining",
    "x-uw-req-per-minute-reset",
)
ET = ZoneInfo("America/New_York")


def api_key() -> str:
    key = os.getenv("UW_API_KEY", "").strip()
    if not key:
        sys.exit("UW_API_KEY is not set in the environment.")
    return key


def request(path: str, params: list[tuple[str, str]] | None, timeout: float):
    """Return (status, latency_ms, headers_subset, json_or_None, error)."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Accept": "application/json",
            "User-Agent": "quiet-alpha-probe/1.0",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            latency = int((time.monotonic() - started) * 1000)
            headers = {k: resp.headers.get(k) for k in RATE_HEADERS if resp.headers.get(k)}
            try:
                data = json.loads(body.decode("utf-8"))
            except ValueError:
                return resp.status, latency, headers, None, "invalid json"
            return resp.status, latency, headers, data, None
    except urllib.error.HTTPError as error:
        latency = int((time.monotonic() - started) * 1000)
        headers = {k: error.headers.get(k) for k in RATE_HEADERS if error.headers.get(k)}
        return error.code, latency, headers, None, f"HTTP {error.code}"
    except TimeoutError:
        return None, int((time.monotonic() - started) * 1000), {}, None, "timeout"
    except urllib.error.URLError as error:
        reason = type(error.reason).__name__ if error.reason else "URLError"
        kind = "timeout" if "timed out" in str(error.reason) else reason
        return None, int((time.monotonic() - started) * 1000), {}, None, kind


def rows(data) -> list:
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            return [inner]
        return []
    return data if isinstance(data, list) else []


def dec(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def parse_ts(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


# ---------------------------------------------------------------------------
def cmd_headers(args):
    for ticker in args.tickers:
        status, latency, headers, data, error = request(
            f"/api/stock/{ticker}/option-contracts", [("limit", "5")], args.timeout
        )
        print(f"\n[{ticker}] status={status} latency={latency}ms error={error}")
        print("  rows:", len(rows(data)))
        for key, value in headers.items():
            print(f"  {key}: {value}")
        sample = rows(data)[:1]
        if sample:
            print("  fields:", ", ".join(sorted(sample[0].keys())))


def cmd_chain(args):
    expiry = args.expiry or datetime.now(ET).date().isoformat()
    option_type = args.direction
    for ticker in args.tickers:
        collected = []
        latencies = []
        for page in range(args.max_pages):
            status, latency, headers, data, error = request(
                f"/api/stock/{ticker}/option-contracts",
                [("expiry", expiry), ("option_type", option_type),
                 ("limit", "500"), ("page", str(page))],
                args.timeout,
            )
            latencies.append(latency)
            if error:
                print(f"[{ticker}] page {page}: {error} (status {status})")
                break
            chunk = rows(data)
            collected.extend(chunk)
            if len(chunk) < 500:
                break
        roots = {}
        for row in collected:
            symbol = str(row.get("option_symbol", ""))
            root = "".join(ch for ch in symbol[:6] if ch.isalpha())
            roots[root] = roots.get(root, 0) + 1
        in_range = []
        for row in collected:
            ask = dec(row.get("nbbo_ask"))
            bid = dec(row.get("nbbo_bid"))
            if ask is not None and Decimal("2.50") <= ask <= Decimal("4.00"):
                in_range.append((row.get("option_symbol"), bid, ask,
                                 row.get("volume"), row.get("open_interest"),
                                 row.get("delta"), row.get("last_tape_time")))
        print(f"\n[{ticker}] expiry={expiry} type={option_type} rows={len(collected)} "
              f"pages_latency_ms={latencies} roots={roots}")
        print(f"  contracts with Ask in $2.50–$4.00: {len(in_range)}")
        in_range.sort(key=lambda r: (r[2] - r[1]) if r[1] is not None else Decimal(99))
        for item in in_range[: args.show]:
            symbol, bid, ask, vol, oi, delta, tape = item
            print(f"  {symbol} bid={bid} ask={ask} spread={ask - bid if bid is not None else '—'} "
                  f"vol={vol} oi={oi} delta={delta} tape={tape}")


def cmd_flow(args):
    for path in (f"/api/stock/{args.ticker}/net-prem-ticks",
                 f"/api/stock/{args.ticker}/flow-alerts",
                 f"/api/stock/{args.ticker}/stock-state"):
        status, latency, headers, data, error = request(path, None, args.timeout)
        data_rows = rows(data)
        print(f"\n{path} status={status} latency={latency}ms rows={len(data_rows)} error={error}")
        if data_rows:
            print("  fields:", ", ".join(sorted(data_rows[-1].keys())))
            print("  last row:", json.dumps(data_rows[-1])[:600])


def cmd_track(args):
    entry = dec(args.entry) if args.entry else None
    ticker = args.ticker
    started = time.time()
    records = []
    statuses = {}
    previous = None
    writer = None
    out_file = None
    if args.out:
        out_file = open(args.out, "w", newline="", encoding="utf-8")
        writer = csv.writer(out_file)
        writer.writerow(["polled_at_utc", "status", "latency_ms", "bid", "ask",
                         "last", "last_tape_time", "tape_age_s", "changed", "error",
                         "minute_remaining"])
    try:
        while time.time() - started < args.duration:
            loop_started = time.time()
            status, latency, headers, data, error = request(
                f"/api/stock/{ticker}/option-contracts",
                [("option_symbol[]", args.symbol)],
                args.timeout,
            )
            now = datetime.now(timezone.utc)
            statuses[status or error] = statuses.get(status or error, 0) + 1
            row = next((r for r in rows(data) if r.get("option_symbol") == args.symbol), None)
            bid = dec(row.get("nbbo_bid")) if row else None
            ask = dec(row.get("nbbo_ask")) if row else None
            last = dec(row.get("last_price")) if row else None
            tape = parse_ts(row.get("last_tape_time")) if row else None
            tape_age = (now - tape).total_seconds() if tape else None
            current = (bid, ask)
            changed = None if previous is None else current != previous
            if row:
                previous = current
            record = dict(at=now, status=status, latency=latency, bid=bid, ask=ask,
                          last=last, tape=tape, tape_age=tape_age, changed=changed,
                          error=error if row or error else "NOT_RETURNED")
            records.append(record)
            if writer:
                writer.writerow([now.isoformat(), status, latency, bid, ask, last,
                                 tape.isoformat() if tape else "", tape_age, changed,
                                 record["error"] or "",
                                 headers.get("x-uw-req-per-minute-remaining", "")])
                out_file.flush()
            print(f"{now:%H:%M:%S} {status} {latency}ms bid={bid} ask={ask} last={last} "
                  f"tape_age={None if tape_age is None else round(tape_age)}s "
                  f"changed={changed} {record['error'] or ''} "
                  f"remaining/min={headers.get('x-uw-req-per-minute-remaining', '?')}")
            if status == 429:
                reset = headers.get("x-uw-req-per-minute-reset")
                print(f"  429 received; reset header={reset}")
            elapsed = time.time() - loop_started
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        if out_file:
            out_file.close()
    summarize_track(records, statuses, args.interval, entry)


def summarize_track(records, statuses, interval, entry):
    ok = [r for r in records if r["bid"] is not None]
    latencies = [r["latency"] for r in records if r["latency"] is not None]
    changes = [r for r in ok if r["changed"]]
    unchanged = [r for r in ok if r["changed"] is False]
    print("\n================ SUMMARY ================")
    print(f"requests: {len(records)}  statuses: {statuses}")
    if latencies:
        print(f"latency ms: p50={pct(latencies, .5)} p95={pct(latencies, .95)} max={max(latencies)}")
    if ok:
        print(f"quotes returned: {len(ok)}  changed vs previous poll: {len(changes)} "
              f"unchanged: {len(unchanged)}")
        streak = longest = 0
        for r in ok:
            streak = streak + 1 if r["changed"] is False else 0
            longest = max(longest, streak)
        print(f"longest unchanged streak: {longest} polls (~{longest * interval:.0f}s)")
        ages = [r["tape_age"] for r in ok if r["tape_age"] is not None]
        if ages:
            print(f"last_tape_time age s: p50={pct(ages, .5):.0f} p95={pct(ages, .95):.0f} "
                  f"max={max(ages):.0f}")
        bids = [r["bid"] for r in ok]
        print(f"bid range: {min(bids)} – {max(bids)}")
        spreads = [r["ask"] - r["bid"] for r in ok if r["ask"] is not None]
        if spreads:
            print(f"spread: min={min(spreads)} max={max(spreads)}")
    if entry and ok:
        print(f"\nmilestone visibility with entry {entry} at {interval}s polling:")
        levels = {}
        for r in ok:
            gain = (r["bid"] - entry) / entry * 100
            for level in range(10, 1000, 10):
                if gain >= level and level not in levels:
                    levels[level] = r["at"]
        for level, at in sorted(levels.items()):
            print(f"  +{level}% first observed at {at:%H:%M:%S} UTC")
        target = entry + Decimal("1.00")
        above = [r for r in ok if r["bid"] >= target]
        if above:
            print(f"  SUCCESS (bid >= {target}) first observed at {above[0]['at']:%H:%M:%S} UTC; "
                  f"{len(above)} polls at/above target")
        else:
            print(f"  SUCCESS (bid >= {target}) not observed")


def cmd_burst(args):
    if not args.yes:
        sys.exit("burst sends rapid requests to discover the per-minute limit. "
                 "Re-run with --yes to confirm.")
    for i in range(args.n):
        status, latency, headers, data, error = request(
            f"/api/stock/{args.ticker}/option-contracts", [("limit", "1")], args.timeout
        )
        print(f"{i + 1:03d} status={status} {latency}ms "
              + " ".join(f"{k.replace('x-uw-', '')}={v}" for k, v in headers.items()))
        if status == 429:
            print("Stopped at first 429.")
            break
        time.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=float, default=10.0)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("headers")
    p.add_argument("--tickers", nargs="+", default=["SPX", "SPXW"])
    p.set_defaults(func=cmd_headers)

    p = sub.add_parser("chain")
    p.add_argument("--direction", choices=["call", "put"], default="call")
    p.add_argument("--expiry", help="YYYY-MM-DD (default: today ET)")
    p.add_argument("--tickers", nargs="+", default=["SPX", "SPXW"])
    p.add_argument("--max-pages", type=int, default=4)
    p.add_argument("--show", type=int, default=10)
    p.set_defaults(func=cmd_chain)

    p = sub.add_parser("flow")
    p.add_argument("--ticker", default="SPX")
    p.set_defaults(func=cmd_flow)

    p = sub.add_parser("track")
    p.add_argument("--symbol", required=True)
    p.add_argument("--ticker", default="SPX")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--duration", type=float, default=600.0)
    p.add_argument("--entry")
    p.add_argument("--out")
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("burst")
    p.add_argument("--ticker", default="SPX")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--interval", type=float, default=0.5)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_burst)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

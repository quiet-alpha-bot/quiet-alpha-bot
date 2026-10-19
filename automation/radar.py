"""Leader-only hourly 0DTE liquidity radar, ledgered and restart-safe."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from . import queries as q
from .models import money
from .occ import strike_text
from .timeutil import et_date, et_datetime, to_et, utcnow

logger = logging.getLogger("quiet-alpha.radar")

RTL = "\u200f"
SA = ZoneInfo("Asia/Riyadh")


def compact(value):
    if value >= Decimal("1000000"):
        return f"${value / Decimal('1000000'):.1f}M"
    if value >= Decimal("1000"):
        return f"${value / Decimal('1000'):.1f}K"
    return f"${money(value)}"


def arabic_amount(value):
    if value >= Decimal("1000000"):
        return f"{value / Decimal('1000000'):.1f} مليون دولار"
    if value >= Decimal("1000"):
        return f"{value / Decimal('1000'):.1f} ألف دولار"
    return f"{money(value)} دولار"


def clock_text(at):
    hour = at.hour % 12 or 12
    suffix = "صباحًا" if at.hour < 12 else "مساءً"
    return f"{hour}:{at.minute:02d} {suffix}"


def radar_header(at, opening):
    return [
        "🦋 <b>QUIET ALPHA — رادار السيولة</b>",
        "<b>SPX · قراءة الافتتاح</b>" if opening else "<b>SPX · تحديث السيولة</b>",
        "", f"🇺🇸 <b>نيويورك: {clock_text(to_et(at))}</b>",
        f"🇸🇦 <b>السعودية: {clock_text(at.astimezone(SA))}</b>",
    ]


def radar_text(snapshot, at, opening=False):
    calls, puts = snapshot.strongest("CALL"), snapshot.strongest("PUT")
    lines = radar_header(at, opening)
    call_total = sum((c.premium for c in snapshot.contracts.values() if c.direction == "CALL"), Decimal(0))
    put_total = sum((c.premium for c in snapshot.contracts.values() if c.direction == "PUT"), Decimal(0))
    if call_total == put_total:
        reading = "النشاط المرصود متوازن بين عقود CALL وPUT."
    else:
        dominant, other = ("CALL", "PUT") if call_total > put_total else ("PUT", "CALL")
        strongest = (calls if dominant == "CALL" else puts)[0]
        reading = (f"التمركز الأكبر في عقود {dominant}، وأبرز نشاط عند "
                   f"<b>{strike_text(strongest.strike)}</b>، مع حضور أقل لعقود {other}.")
    lines += ["", "<b>قراءة السيولة</b>", reading, "", "📊 <b>مراكز السيولة</b>", ""]
    # Select strongest three on each side, then order the combined six like
    # the price axis of a chart, not by premium and not grouped by side.
    for flow in sorted(calls + puts, key=lambda c: (-c.strike, c.direction)):
        side = "🟢" if flow.direction == "CALL" else "🔴"
        lines.append(f"{side} <b>{strike_text(flow.strike)} {flow.direction}</b> — "
                     f"<code>{compact(flow.premium)}</code>")
    lines += ["", "🦋 <b>QUIET ALPHA — تحليل السيولة</b>"]
    for direction, side, flows in (("CALL", "🟢", calls), ("PUT", "🔴", puts)):
        if direction == "PUT":
            lines.append("")
        lines.append(f"{side} <b>{direction}</b>")
        if flows:
            flow = flows[0]
            lines += [f"النشاط الأبرز عند <b>{strike_text(flow.strike)}</b>",
                      f"حجم التداول: <b>{flow.volume:,} عقد</b>",
                      f"قيمة التدفقات: <b>{arabic_amount(flow.premium)}</b>"]
        else:
            lines.append("لا توجد تدفقات مرصودة لهذا الجانب ضمن الفترة.")
    lines += ["", f"فترة الرصد: {to_et(snapshot.start):%H:%M}–{to_et(snapshot.end):%H:%M} نيويورك · عقود اليوم",
              "", "⚠️ <b>تنبيه</b>",
              "المحتوى قراءة تحليلية لتدفقات الخيارات المنفّذة، ولا يُعدّ توصية دخول أو شراء أو بيع."]
    return "\n".join(RTL + line if line else "" for line in lines)


def report_slot(now, market):
    local = to_et(now)
    if local.weekday() >= 5:
        return None
    opened = et_datetime(et_date(now), market.open_et)
    closed = et_datetime(et_date(now), market.close_et)
    if now < opened or now >= closed:
        return None
    slot = int((now - opened).total_seconds() // 3600)
    due = opened + timedelta(hours=slot)
    # Only the current slot is eligible, but it remains retryable throughout
    # its hour. A restart after minute ten must not silence the radar.
    return et_date(now), slot, due, opened


class LiquidityRadar:
    def __init__(self, settings, db, publisher, liquidity):
        self.s, self.db, self.publisher, self.liquidity = settings, db, publisher, liquidity
        self.last_error = None
        self._retry_after = None

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("Liquidity radar tick failed")
            await asyncio.sleep(30)

    async def build(self, now, slot, due, opened):
        # On a delayed retry use the most recent rolling hour, not an old
        # fixed window that can no longer satisfy the freshness guard.
        end = now.replace(second=0, microsecond=0)
        start = max(opened, end - timedelta(hours=1))
        snapshot = await self.liquidity.radar_snapshot(start, end)
        observed_at = utcnow()
        if not snapshot.fresh(observed_at, max_age=180):
            self.last_error = snapshot.reason or "WAITING_FOR_FRESH_SESSION_DATA"
            return None
        return radar_text(snapshot, observed_at, slot == 0)

    async def tick(self):
        now = utcnow()
        scheduled = report_slot(now, self.s.market)
        if scheduled is None:
            return
        day, slot, due, opened = scheduled
        ident = int(day.strftime("%Y%m%d"))
        event = await self.db.fetchrow(q.EVENT_STATUS, "radar", ident, "LIQUIDITY_RADAR", slot)
        if event and event["status"] not in ("FAILED",):
            return
        saved = await self.db.fetchrow(q.GET_RADAR_REPORT, day, slot)
        if saved is None:
            if self._retry_after is not None and now < self._retry_after:
                return
            text = await self.build(now, slot, due, opened)
            self._retry_after = utcnow() + timedelta(seconds=60)
        else:
            text = saved["message_text"]
        if text is None:
            header = radar_header(now, slot == 0)
            wait = "\n".join(RTL + l if l else "" for l in header)
            wait += "\n\n" + RTL + "بانتظار بيانات مكتملة وحديثة لعقود اليوم؛ لا تُعرض بيانات الجلسة السابقة."
            await self.publisher.text_once("radar", ident, "RADAR_WAIT", wait, level=slot,
                                           payload={"reason": self.last_error})
            return
        if saved is None:
            saved = await self.db.fetchrow(q.SAVE_RADAR_REPORT, day, slot, text)
            text = saved["message_text"]
        await self.publisher.text_once("radar", ident, "LIQUIDITY_RADAR", text, level=slot,
                                       payload={"scope": "SPXW_0DTE", "slot": slot},
                                       max_attempts=1000000)
        self.last_error = None

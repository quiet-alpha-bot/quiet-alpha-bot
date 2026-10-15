"""Text messages and inline keyboards for the private channel (no I/O)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from html import escape
from typing import Optional, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .milestones import gain_pct, progress_items, success_price
from .models import money
from .occ import strike_text
from .timeutil import display_time

CB_PREFIX = "qa"
CB_APPROVE = "ap"
CB_REJECT = "rj"
CB_RECONFIRM = "rc"
CB_CANCEL = "cn"


def callback_data(action: str, proposal_id: int) -> str:
    return f"{CB_PREFIX}:{action}:{proposal_id}"


def parse_callback(data: Optional[str]) -> Optional[tuple[str, int]]:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CB_PREFIX or not parts[2].isdigit():
        return None
    if parts[1] not in {CB_APPROVE, CB_REJECT, CB_RECONFIRM, CB_CANCEL}:
        return None
    return parts[1], int(parts[2])


def proposal_keyboard(proposal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ APPROVE", callback_data=callback_data(CB_APPROVE, proposal_id)),
            InlineKeyboardButton("❌ REJECT", callback_data=callback_data(CB_REJECT, proposal_id)),
        ]]
    )


def reconfirm_keyboard(proposal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🔁 RECONFIRM", callback_data=callback_data(CB_RECONFIRM, proposal_id)),
            InlineKeyboardButton("✖️ CANCEL", callback_data=callback_data(CB_CANCEL, proposal_id)),
        ]]
    )


def usd(value: Optional[Decimal]) -> str:
    return "—" if value is None else f"${money(value)}"


def compact_usd(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    amount = abs(value)
    for unit, size in (("B", Decimal(1e9)), ("M", Decimal(1e6)), ("K", Decimal(1e3))):
        if amount >= size:
            return f"{sign}${(amount / size).quantize(Decimal('0.1'))}{unit}"
    return f"{sign}${amount.quantize(Decimal('1'))}"


def contract_label(strike: Decimal, direction: str, root: str = "SPXW") -> str:
    return f"{root} {strike_text(strike)} {direction}"


UW_STATUS_TEXT = {
    "BOTH_CONFIRMED": "✅ UW WATCH بنفس الاتجاه",
    "CONFLICT": "⚠️ UW WATCH بعكس الاتجاه",
    "INDICATOR_ONLY": "— لا يوجد UW WATCH",
    None: "— التصنيف غير مفعّل",
}


def strength_label(value: Decimal) -> str:
    """Arabic rating for the pre-approval composite strength only."""
    if value >= Decimal("80"):
        return "قوية"
    if value >= Decimal("65"):
        return "جيدة"
    if value >= Decimal("50"):
        return "متوسطة"
    return "ضعيفة"


def strength_pct(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    return f"{value.quantize(Decimal('1'))}%"


def proposal_text(
    proposal_id: int,
    direction: str,
    symbol: str,
    strike: Decimal,
    expiry_text: str,
    suggested_entry: Decimal,
    bid: Optional[Decimal],
    ask: Optional[Decimal],
    last: Optional[Decimal],
    success_gain: Decimal,
    classification: Optional[str],
    watch_engine_active: Optional[bool],
    uw_context: Optional[str],
    reasons: Sequence[str],
    signal_time: datetime,
    event_type: Optional[str],
    expires_at: Optional[datetime],
    tz_name: str,
    tv_strength: Optional[Decimal] = None,
    uw_strength: Optional[Decimal] = None,
    composite_strength: Optional[Decimal] = None,
    uw_aligned: Optional[bool] = None,
) -> str:
    spread = (ask - bid) if (bid is not None and ask is not None) else None
    status = UW_STATUS_TEXT.get(classification, classification or "—")
    if watch_engine_active is False and classification == "INDICATOR_ONLY":
        status = "— UW WATCH غير نشط وقت الإشارة"
    lines = [
        "🦋 <b>QUIET ALPHA — TRADE PROPOSAL</b>",
        "",
        f"Direction: <b>{escape(direction)}</b>",
        f"Source: {escape(classification or 'N/A')}",
        f"UW Confirmation: {escape(status)}",
    ]
    if composite_strength is not None:
        alignment = (
            "متوافق ✅" if uw_aligned is True else
            "معاكس ⚠️" if uw_aligned is False else
            "غير متوفر"
        )
        lines.extend(
            [
                f"قوة الصفقة: <b>{strength_pct(composite_strength)} — {strength_label(composite_strength)}</b>",
                f"TradingView: {strength_pct(tv_strength)}",
                f"UW: {strength_pct(uw_strength)} · {alignment}",
            ]
        )
    lines.extend(
        [
        "Contract: SPXW",
        f"Strike: <b>{escape(strike_text(strike))}</b>",
        f"Expiration: {escape(expiry_text)}",
        f"Symbol: <code>{escape(symbol)}</code>",
        "",
        f"Suggested Entry: <b>{usd(suggested_entry)}</b>",
        f"Bid: {usd(bid)}",
        f"Ask: {usd(ask)}",
        f"Spread: {usd(spread)}",
        f"Current Premium (Last): {usd(last)}",
        f"Success Level: <b>{usd(success_price(suggested_entry, success_gain))}</b>",
        "",
        f"Signal Time: {display_time(signal_time, tz_name)}"
        + (f" · {escape(event_type)}" if event_type else ""),
        ]
    )
    if reasons:
        lines.append("Selection: " + escape(" · ".join(reasons)))
    if uw_context:
        lines.append("UW Context: " + escape(uw_context))
    if expires_at is not None:
        lines.append(f"⏳ صالح حتى: {display_time(expires_at, tz_name)}")
    lines.append(f"<i>Proposal #{proposal_id} · السعر عند الموافقة يُعاد طلبه من UW</i>")
    return "\n".join(lines)


def decision_footer(text: str, footer: str) -> str:
    return f"{text}\n\n{footer}"


def reconfirm_text(
    proposal_id: int,
    label: str,
    suggested: Decimal,
    current: Decimal,
    bid: Optional[Decimal],
    success_gain: Decimal,
    reason: str,
) -> str:
    return "\n".join(
        [
            "🔁 <b>QUIET ALPHA — RECONFIRM</b>",
            "",
            escape(label),
            f"Suggested Price: {usd(suggested)}",
            f"Current Price (Ask): <b>{usd(current)}</b>",
            f"Bid: {usd(bid)}",
            f"Success Level: {usd(success_price(current, success_gain))}",
            "",
            f"السبب: {escape(reason)}",
            f"<i>Proposal #{proposal_id}</i>",
        ]
    )


def tracking_text(
    label: str,
    entry: Decimal,
    current_bid: Optional[Decimal],
    highest_bid: Optional[Decimal],
    highest_milestone: int,
    step_pct: int,
    success_detected: bool,
    success_gain: Decimal,
    classification: Optional[str],
    updated_at: datetime,
    tz_name: str,
    closed_reason: Optional[str] = None,
    stale_note: Optional[str] = None,
) -> str:
    perf = gain_pct(entry, current_bid) if current_bid is not None else None
    perf_text = "—" if perf is None else f"{perf.quantize(Decimal('1'))}%"
    if perf is not None and perf > 0:
        perf_text = "+" + perf_text
    lines = [
        "🦋 <b>QUIET ALPHA | LIVE TRACKING</b>",
        "",
        escape(label),
        "",
        f"Entry: {usd(entry)}",
        f"Current: {usd(current_bid)}",
        f"Highest: {usd(highest_bid)}",
        f"Performance: {perf_text}",
        "",
        f"Source: {escape(classification or 'N/A')}",
        "",
    ]
    items = progress_items(entry, highest_milestone, step_pct, success_detected, success_gain)
    success_dollars = (success_gain * Decimal("100")).quantize(Decimal("1"))
    for _, item in items:
        lines.append(
            f"⭐ +${success_dollars} SUCCESS"
            if item == "SUCCESS"
            else f"✅ {item}"
        )
    if not items:
        lines.append("⏳ بانتظار +10%")
    lines.append("")
    if stale_note:
        lines.append(f"⚠️ {escape(stale_note)}")
    if closed_reason:
        lines.append(f"⏹ CLOSED — {escape(closed_reason)}")
    lines.append(f"Last Update: {display_time(updated_at, tz_name)}")
    lines.append("<i>Prices = Observed Bid (UW REST)</i>")
    return "\n".join(lines)


def milestone_notification(
    label: str,
    level: int,
    bid: Decimal,
    start_level: Optional[int] = None,
    elapsed_seconds: Optional[float] = None,
) -> str:
    if start_level is not None and start_level < level:
        progress = f"+{start_level}% → +{level}%"
    else:
        progress = f"+{level}%"
    elapsed = ""
    if elapsed_seconds is not None:
        total = max(0, int(round(elapsed_seconds)))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            duration = f"{hours}h {minutes}m {seconds}s"
        elif minutes:
            duration = f"{minutes}m {seconds}s"
        else:
            duration = f"{seconds}s"
        elapsed = f" | Time: {duration}"
    return f"🦋 {escape(label)} | {progress} | Bid {usd(bid)}{elapsed}"


def watch_notification(
    direction: str,
    net_premium: Decimal,
    strength: Decimal,
    expires_at: datetime,
    tz_name: str,
) -> str:
    return (
        f"👁 <b>UW WATCH</b> — {escape(direction)}\n"
        f"Net Premium: {compact_usd(net_premium)} · Strength {strength.quantize(Decimal('0.01'))}\n"
        f"Indicator: PENDING حتى {display_time(expires_at, tz_name)}\n"
        "<i>Shadow — لا ينشئ صفقة</i>"
    )


def signal_status_text(direction: str, status: str, reason: Optional[str], signal_id: str) -> str:
    return (
        f"🦋 <b>Signal {escape(direction)}</b> — {escape(status)}\n"
        + (f"{escape(reason)}\n" if reason else "")
        + f"<code>{escape(signal_id)}</code>"
    )

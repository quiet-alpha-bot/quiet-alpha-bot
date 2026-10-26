"""Daily report: peak Bid gains and losses at tracked closing Bid."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from html import escape
from typing import Sequence
from zoneinfo import ZoneInfo

from .models import money
from .occ import strike_text


ARABIC_MONTHS = (
    "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)


def _signed_usd(value: Decimal) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${money(abs(value))}"


def _peak_result(trade) -> Decimal:
    entry = Decimal(trade["approved_entry"])
    highest = trade["highest_bid"]
    if highest is None:
        return Decimal("0")
    return (Decimal(highest) - entry) * Decimal("100")


def summarize_daily_trades(trades: Sequence, success_dollars: Decimal) -> dict:
    neutral = [t["close_reason"] == "BREAKEVEN_EXIT" for t in trades]
    results = [
        ((Decimal(t["current_bid"]) - Decimal(t["approved_entry"])) * Decimal("100"))
        if flat and t["current_bid"] is not None else _peak_result(t)
        for t, flat in zip(trades, neutral)
    ]
    wins = sum(result >= success_dollars and not flat for result, flat in zip(results, neutral))
    losses = len(results) - wins - sum(neutral)
    positive = sum((max(result, Decimal("0")) for result in results), Decimal("0"))
    # current_bid is frozen when status becomes CLOSED: quote updates only
    # apply to TRACKING rows. This is an observed price, not a broker fill.
    closed_results = [
        (Decimal(t["current_bid"]) - Decimal(t["approved_entry"])) * Decimal("100")
        for t in trades
        if t["status"] == "CLOSED" and t["current_bid"] is not None
    ]
    missing_close_prices = sum(
        t["status"] == "CLOSED" and t["current_bid"] is None for t in trades
    )
    negative = sum((min(result, Decimal("0")) for result in closed_results), Decimal("0"))
    total = positive + negative
    win_rate = (
        Decimal(wins) * Decimal("100") / Decimal(len(results))
        if results else Decimal("0")
    )
    best_index = max(range(len(results)), key=results.__getitem__) if results else None
    return {
        "total": len(results),
        "missing_close_prices": missing_close_prices,
        "wins": wins,
        "breakeven": sum(neutral),
        "losses": losses,
        "win_rate": win_rate,
        "positive": positive,
        "negative": negative,
        "net": total,
        "best_index": best_index,
        "results": results,
        "stop_loss": sum(t["close_reason"] in {"STOP_LOSS_30", "STOP_LOSS_50"} for t in trades),
        "protected": sum(t["close_reason"] == "PROTECTED_STOP" for t in trades),
        "session_end": sum(t["close_reason"] == "SESSION_END" for t in trades),
    }


def trade_timing(trade) -> str:
    """Telegram publication to first occurrence of the tracked peak; no backfill."""
    published = trade.get("published_at")
    peak = trade.get("highest_bid_time")
    time_text = duration = "—"
    if isinstance(published, datetime) and published.tzinfo is not None:
        local = published.astimezone(ZoneInfo("Asia/Riyadh"))
        time_text = f"{local.hour % 12 or 12}:{local.minute:02d} {'ص' if local.hour < 12 else 'م'}"
        if isinstance(peak, datetime) and peak.tzinfo is not None:
            elapsed = (peak - published).total_seconds()
            if elapsed >= 0:
                hours, rest = divmod(int(elapsed), 3600)
                minutes, seconds = divmod(rest, 60)
                parts = []
                if hours:
                    parts.append(f"{hours}س")
                if minutes:
                    parts.append(f"{minutes}د")
                if seconds or not parts:
                    parts.append(f"{seconds}ث")
                duration = " ".join(parts)
    return f"🕒 {time_text} | ⏱ {duration}"


def daily_report_text(
    trades: Sequence,
    day: date,
    success_premium_gain: Decimal,
    preview: bool = False,
) -> tuple[str, dict]:
    success_dollars = success_premium_gain * Decimal("100")
    summary = summarize_daily_trades(trades, success_dollars)
    title = "📊 <b>QUIET ALPHA — البيان اليومي</b>"
    if preview:
        title += " <i>(معاينة)</i>"
    lines = [
        title,
        f"{day.day} {ARABIC_MONTHS[day.month]} {day.year}",
        "",
        f"إجمالي الصفقات: <b>{summary['total']}</b>",
        f"✅ رابحة: <b>{summary['wins']}</b>",
        f"❌ خاسرة: <b>{summary['losses']}</b>",
        f"⚖️ خروج على رأس المال: <b>{summary['breakeven']}</b>",
        f"نسبة النجاح: <b>{summary['win_rate'].quantize(Decimal('0.1'))}%</b>",
        "",
        f"📈 مجموع إجمالي الربح: ${money(summary['positive'])}",
        f"📉 مجموع إجمالي الخسارة: {'-' if summary['negative'] < 0 else ''}${money(abs(summary['negative']))}",
        f"💰 صافي الربح: <b>{'-' if summary['net'] < 0 else ''}${money(abs(summary['net']))}</b>"
        f" ≈ <b>{'-' if summary['net'] < 0 else ''}{money(abs(summary['net']) * Decimal('3.75'))} ر.س</b>",
    ]

    if trades:
        lines.extend(["", "<b>تفاصيل الصفقات</b>", "سعر الدخول → أعلى سعر مرصود", "🕒 الطرح بتوقيت السعودية · ⏱ المدة إلى أعلى سعر"])
        for index, (trade, result) in enumerate(
            zip(trades[:30], summary["results"][:30]), start=1
        ):
            entry = Decimal(trade["approved_entry"])
            highest = trade["highest_bid"]
            flat = trade["close_reason"] == "BREAKEVEN_EXIT"
            won = result >= success_dollars and not flat
            label = (
                f"{strike_text(Decimal(trade['strike']))} {trade['direction']}"
            )
            lines.append(
                f"{index}. {'⚖️' if flat else ('✅' if won else '❌')} <code>{escape(label)} | "
                f"{money(entry)} → {money(trade['current_bid']) if flat else (money(highest) if highest is not None else '—')} "
                f"| {_signed_usd(result)}</code>"
                + (" — خروج على رأس المال" if flat else "")
            )
            lines.append(trade_timing(trade))
        if len(trades) > 30:
            lines.append(f"… و{len(trades) - 30} صفقات أخرى")

    if summary["missing_close_prices"]:
        lines.append(f"⚠️ {summary['missing_close_prices']} صفقة مغلقة بلا سعر إغلاق؛ خسائرها غير مكتملة.")
    payload = {key: str(value) for key, value in summary.items() if key != "results"}
    return "\n".join(lines), payload


__all__ = ["daily_report_text", "summarize_daily_trades"]

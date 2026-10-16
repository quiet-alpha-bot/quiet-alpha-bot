"""Daily text performance report based on each trade's highest observed Bid."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape
from typing import Sequence

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
    results = [_peak_result(trade) for trade in trades]
    wins = sum(result >= success_dollars for result in results)
    losses = len(results) - wins
    positive = sum((max(result, Decimal("0")) for result in results), Decimal("0"))
    negative = sum((min(result, Decimal("0")) for result in results), Decimal("0"))
    total = sum(results, Decimal("0"))
    win_rate = (
        Decimal(wins) * Decimal("100") / Decimal(len(results))
        if results else Decimal("0")
    )
    best_index = max(range(len(results)), key=results.__getitem__) if results else None
    return {
        "total": len(results),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "positive": positive,
        "negative": negative,
        "net": total,
        "best_index": best_index,
        "results": results,
        "stop_loss": sum(t["close_reason"] == "STOP_LOSS_30" for t in trades),
        "protected": sum(t["close_reason"] == "PROTECTED_STOP" for t in trades),
        "session_end": sum(t["close_reason"] == "SESSION_END" for t in trades),
    }


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
        f"نسبة النجاح: <b>{summary['win_rate'].quantize(Decimal('0.1'))}%</b>",
        "",
        f"📈 مجموع إجمالي الربح: ${money(summary['positive'])}",
        f"📉 مجموع إجمالي الخسارة: {'-' if summary['negative'] < 0 else ''}${money(abs(summary['negative']))}",
        f"💰 صافي الربح: <b>{'-' if summary['net'] < 0 else ''}${money(abs(summary['net']))}</b>",
    ]

    if trades:
        lines.extend(["", "<b>تفاصيل الصفقات</b>"])
        for index, (trade, result) in enumerate(
            zip(trades[:12], summary["results"][:12]), start=1
        ):
            entry = Decimal(trade["approved_entry"])
            highest = trade["highest_bid"]
            won = result >= success_dollars
            label = (
                f"SPXW {strike_text(Decimal(trade['strike']))} {trade['direction']}"
            )
            lines.append(
                f"{index}. {'✅' if won else '❌'} {escape(label)} · "
                f"دخول ${money(entry)} · أعلى ${money(highest) if highest is not None else '—'} "
                f"· {_signed_usd(result)}"
            )
        if len(trades) > 12:
            lines.append(f"… و{len(trades) - 12} صفقات أخرى")

    lines.extend([
        "",
        f"<i>التصنيف يعتمد على أعلى سعر تم تحقيقه: النجاح يبدأ من +${money(success_dollars)}.</i>",
    ])
    payload = {key: str(value) for key, value in summary.items() if key != "results"}
    return "\n".join(lines), payload


__all__ = ["daily_report_text", "summarize_daily_trades"]

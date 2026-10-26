"""Wires the automation layer into the existing python-telegram-bot app.

Called from main.py only when AUTOMATION_ENABLED=true:

    setup_automation(application, renderers)

Guarantees:
* Never raises into main.py; any failure leaves the manual bot running.
* Telegram polling stays exactly as it is (run_polling in main.py).
* The webhook server, UW watch engine and tracker live in the same process,
  started from post_init and stopped from post_shutdown.
* Watch engine, tracker and sweeps run only on the instance holding the
  PostgreSQL advisory lock (no duplicates during Railway redeploys).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from . import queries as q
from .approval import ApprovalHandler
from .manual_tracking import ManualTracking
from .contract_support import ContractSupport
from .cards import CardRenderers, Publisher
from .config import Settings, load_settings
from .db import Database, LeaderLock
from .daily_report import daily_report_text
from .pipeline import SignalPipeline
from .liquidity import LiquidityService
from .radar import LiquidityRadar, radar_text
from .telegram_ui import decision_footer, usd
from .timeutil import et_date, et_datetime, to_et, utcnow
from .tracker import Tracker
from .uw_client import UWClient
from .watch_engine import WatchEngine
from .webhook import WebhookServer

logger = logging.getLogger("quiet-alpha.runtime")

LEADER_RETRY_SECONDS = 15
MAINTENANCE_SECONDS = 30
DAILY_REPORT_DELAY_MINUTES = 5


class AutomationRuntime:
    def __init__(self, application: Application, settings: Settings,
                 renderers: CardRenderers) -> None:
        self.app = application
        self.settings = settings
        self.renderers = renderers
        self.ready = False
        self.is_leader = False
        self.db: Optional[Database] = None
        self.uw: Optional[UWClient] = None
        self.publisher: Optional[Publisher] = None
        self.pipeline: Optional[SignalPipeline] = None
        self.tracker: Optional[Tracker] = None
        self.watch: Optional[WatchEngine] = None
        self.liquidity = None
        self.radar = None
        self.contract_support = None
        self.webhook: Optional[WebhookServer] = None
        self.approval = ApprovalHandler(self)
        self.manual = ManualTracking(self)
        self._tasks: set[asyncio.Task] = set()
        self._leader_children: list[asyncio.Task] = []
        self._leader_task: Optional[asyncio.Task] = None
        self._lock: Optional[LeaderLock] = None
        self.disabled: list[str] = []

    # -----------------------------------------------------
    # Startup / shutdown (never raise)
    # -----------------------------------------------------
    async def start(self, _app: Application) -> None:
        try:
            await self._start()
        except Exception:
            logger.exception("Automation failed to start — manual bot continues")
            self.ready = False

    async def _start(self) -> None:
        s = self.settings
        problems = s.problems()
        for problem in problems:
            logger.warning("Automation config: %s", problem)

        if not (s.bot_token and s.signal_chat_id and s.database_url):
            logger.error("Automation disabled: core configuration missing")
            return

        self.db = Database(s.database_url)
        await self.db.connect()
        await self.db.migrate()

        self.publisher = Publisher(self.app.bot, s.signal_chat_id, self.db)

        # Effective flags: a component whose requirements are missing is
        # switched off (fail closed) instead of half-running.
        off: dict[str, bool] = {}
        if not s.uw_api_key:
            for name in ("uw_watch", "selection", "tracking", "liquidity_radar", "flow_selection"):
                if getattr(s.flags, name):
                    off[name] = False
                    self.disabled.append(f"{name}: UW_API_KEY missing")
        if s.flags.proposals and s.owner_user_id is None:
            off["proposals"] = False
            self.disabled.append("proposals: OWNER_USER_ID missing/invalid")
        if s.flags.tv_intake and not s.tv_webhook_secret:
            off["tv_intake"] = False
            self.disabled.append("tv_intake: TV_WEBHOOK_SECRET missing")
        if off:
            s = replace(s, flags=replace(s.flags, **off))
            self.settings = s

        if s.uw_api_key:
            self.uw = UWClient(s.uw_api_key, s.uw)
            await self.uw.start()

        if self.uw is not None:
            self.liquidity = LiquidityService(self.uw, s.selection.flow_max_pages)
            self.radar = LiquidityRadar(s, self.db, self.publisher, self.liquidity)
            self.contract_support = ContractSupport(s, self.db, self.publisher, self.liquidity)
        self.pipeline = SignalPipeline(s, self.db, self.uw, self.publisher, self.liquidity)
        self.tracker = Tracker(s, self.db, self.uw, self.publisher, self.renderers)
        if self.uw is not None:
            self.watch = WatchEngine(s, self.db, self.uw, self.publisher)

        self.webhook = WebhookServer(
            db=self.db,
            secret=s.tv_webhook_secret,
            path=s.webhook_path,
            port=s.port,
            max_body_bytes=s.webhook_max_body_bytes,
            intake_enabled=s.flags.tv_intake,
            on_new_signal=self._schedule_signal,
            health=self.health,
        )
        await self.webhook.start()

        self.ready = True
        self._leader_task = asyncio.create_task(self._leader_loop(), name="qa-leader")
        logger.info("Automation started: %s", self.flag_summary())
        await self.publisher.text(
            "🟢 <b>QUIET ALPHA Automation</b> بدأ\n"
            f"<code>{self.flag_summary()}</code>"
            + ("\n⚠️ " + "\n⚠️ ".join(self.disabled) if self.disabled else "")
        )

    async def stop(self, _app: Application) -> None:
        try:
            for task in list(self._leader_children) + list(self._tasks):
                task.cancel()
            if self._leader_task is not None:
                self._leader_task.cancel()
            await asyncio.gather(
                *self._leader_children, *self._tasks,
                *([self._leader_task] if self._leader_task else []),
                return_exceptions=True,
            )
            if self.webhook is not None:
                await self.webhook.stop()
            if self._lock is not None:
                await self._lock.release()
            if self.uw is not None:
                await self.uw.close()
            if self.db is not None:
                await self.db.close()
        except Exception:
            logger.exception("Automation shutdown error")

    # -----------------------------------------------------
    def flag_summary(self) -> str:
        f = self.settings.flags
        names = [name for name, on in vars(f).items() if on]
        return "flags=" + (",".join(names) if names else "none")

    def health(self) -> dict:
        return {
            "automation_ready": self.ready,
            "leader": self.is_leader,
            "flags": {k: v for k, v in vars(self.settings.flags).items()},
        }

    def _spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _schedule_signal(self, signal_db_id: int) -> None:
        if self.pipeline is not None:
            self._spawn(self.pipeline.process(signal_db_id), f"qa-signal-{signal_db_id}")

    # -----------------------------------------------------
    # Leader election + leader-only loops
    # -----------------------------------------------------
    async def _leader_loop(self) -> None:
        assert self.settings.database_url
        self._lock = LeaderLock(self.settings.database_url)
        while True:
            try:
                acquired = await self._lock.try_acquire()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Leader lock unavailable: %s", type(error).__name__)
                acquired = False

            if not acquired:
                self.is_leader = False
                await asyncio.sleep(LEADER_RETRY_SECONDS)
                continue

            self.is_leader = True
            logger.info("This instance is the automation leader")
            self._leader_children = [
                asyncio.create_task(self._maintenance_loop(), name="qa-maintenance")
            ]
            f = self.settings.flags
            if f.liquidity_radar and self.radar is not None:
                self._leader_children.append(asyncio.create_task(self.radar.run(), name="qa-radar"))
            if f.tracking and self.contract_support is not None:
                self._leader_children.append(asyncio.create_task(self.contract_support.run(), name="qa-contract-support"))
            if f.uw_watch and self.watch is not None:
                self._leader_children.append(
                    asyncio.create_task(self.watch.run(), name="qa-watch")
                )
            if f.tracking or f.proposals:
                self._leader_children.append(
                    asyncio.create_task(self.tracker.run(), name="qa-tracker")
                )
            try:
                while await self._lock.still_held():
                    await asyncio.sleep(LEADER_RETRY_SECONDS)
            finally:
                for task in self._leader_children:
                    task.cancel()
                await asyncio.gather(*self._leader_children, return_exceptions=True)
                self._leader_children = []
                self.is_leader = False
            logger.warning("Leader lock lost; retrying")

    async def _maintenance_loop(self) -> None:
        while True:
            try:
                await self._maintenance()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Maintenance tick failed")
            await asyncio.sleep(MAINTENANCE_SECONDS)

    async def _maintenance(self) -> None:
        db = self.db
        assert db is not None and self.publisher is not None
        # Signals stored but never processed (e.g. crash right after insert).
        for row in await db.fetch(q.UNPROCESSED_SIGNALS, 20.0):
            await self._schedule_signal(row["id"])
        await db.fetch(q.FAIL_STALE_PROCESSING, 600.0)
        await db.fetch(q.RECOVER_STUCK_APPROVING, 120.0)
        # Expired proposals: remove buttons.
        for proposal in await db.fetch(q.EXPIRE_PROPOSALS):
            if proposal["classification"] == "MANUAL":
                # Private previews expire silently; never leak them to the channel.
                continue
            for message_id in (proposal["message_id"], proposal["reconfirm_message_id"]):
                if message_id:
                    try:
                        await self.app.bot.edit_message_reply_markup(
                            chat_id=self.settings.signal_chat_id,
                            message_id=message_id,
                            reply_markup=None,
                        )
                    except Exception:
                        pass
            await self.publisher.text(f"⌛ Proposal #{proposal['id']} EXPIRED")
        await self._send_daily_report_if_due()

    async def _send_daily_report_if_due(self) -> None:
        """Send one report after all of today's trades have closed."""
        assert self.db is not None and self.publisher is not None
        now = utcnow()
        local = to_et(now)
        if local.weekday() >= 5:
            return
        day = et_date(now)
        report_at = et_datetime(day, self.settings.market.close_et) + timedelta(
            minutes=DAILY_REPORT_DELAY_MINUTES
        )
        if now < report_at:
            return
        trades = await self.db.fetch(q.DAILY_TRADES, day)
        if not trades or any(trade["status"] == "TRACKING" for trade in trades):
            return
        text, payload = daily_report_text(
            trades, day, self.settings.tracking.success_premium_gain
        )
        await self.publisher.text_once(
            "daily",
            int(day.strftime("%Y%m%d")),
            "DAILY_REPORT",
            text,
            payload=payload,
        )

    # -----------------------------------------------------
    # Owner-only automation commands
    # -----------------------------------------------------
    def _is_owner(self, update: Update) -> bool:
        owner = self.settings.owner_user_id
        user = update.effective_user
        return owner is not None and user is not None and user.id == owner

    async def _guard(self, update: Update) -> bool:
        if update.message is None:
            return False
        if not self._is_owner(update):
            logger.warning(
                "Unauthorized automation command from user %s",
                getattr(update.effective_user, "id", None),
            )
            await update.message.reply_text("⛔ غير مصرح.")
            return False
        if not self.ready:
            await update.message.reply_text("⚠️ الأتمتة غير جاهزة. راجعي السجل.")
            return False
        return True

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        trades = await self.db.fetch(q.TRACKING_TRADES)
        last_obs = await self.db.fetchval(q.LAST_GOOD_OBSERVATION)
        lines = [
            "🦋 <b>QUIET ALPHA — Automation Status</b>",
            f"Leader: {'yes' if self.is_leader else 'no'}",
            self.flag_summary(),
            f"Tracking trades: {len(trades)}",
            f"Last UW watch observation: {last_obs.isoformat() if last_obs else '—'}",
        ]
        if self.uw is not None:
            lines.append(
                f"UW requests (this process): {self.uw.request_count} · "
                f"429: {self.uw.rate_limited_count} · errors: {self.uw.error_count}"
            )
            if self.uw.last_rate:
                lines.append("UW headers: " + ", ".join(
                    f"{k.replace('x-uw-', '')}={v}" for k, v in self.uw.last_rate.items()
                ))
        if self.tracker and self.tracker.last_error:
            lines.append(f"Tracker last error: {self.tracker.last_error}")
        if self.liquidity and self.liquidity.last_error:
            lines.append(f"Liquidity data: {self.liquidity.last_error}")
        if self.radar and self.radar.last_error:
            lines.append(f"Radar data: {self.radar.last_error}")
        if self.watch and self.watch.last_error:
            lines.append(f"Watch last error: {self.watch.last_error}")
        if self.contract_support is not None and self.settings.flags.tracking:
            lines.append("Contract support: enabled · once after success · checks every 60s")
            if self.contract_support.last_error:
                lines.append(f"Contract support data: {self.contract_support.last_error}")
        for warning in self.disabled:
            lines.append(f"⚠️ {warning}")
        for t in trades[:10]:
            lines.append(
                f"#{t['id']} {t['option_symbol']} entry {usd(t['approved_entry'])} "
                f"bid {usd(t['current_bid'])} high {usd(t['highest_bid'])}"
            )
        await update.message.reply_html("\n".join(lines))

    async def cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("الاستخدام: /qa_close <trade_id>")
            return
        closed = await self.tracker.close(int(context.args[0]), "MANUAL_CLOSE")
        await update.message.reply_text(
            "✅ تم إغلاق المتابعة." if closed else "لا توجد صفقة TRACKING بهذا الرقم."
        )

    async def cmd_daily(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Owner-only preview; does not consume the automatic daily report."""
        if not await self._guard(update):
            return
        day = et_date(utcnow())
        if len(context.args) > 1:
            await update.message.reply_text("الاستخدام: /qa_daily أو /qa_daily yesterday أو /qa_daily YYYY-MM-DD")
            return
        if context.args:
            requested = context.args[0].strip().lower()
            if requested in ("yesterday", "أمس", "امس"):
                day -= timedelta(days=1)
            else:
                try:
                    day = date.fromisoformat(requested)
                except ValueError:
                    await update.message.reply_text("التاريخ بصيغة YYYY-MM-DD، أو اكتبي /qa_daily yesterday")
                    return
        trades = await self.db.fetch(q.DAILY_TRADES, day)
        if not trades:
            await update.message.reply_text(f"لا توجد صفقات مسجلة بتاريخ {day.isoformat()} حسب توقيت سوق نيويورك.")
            return
        text, _ = daily_report_text(
            trades, day, self.settings.tracking.success_premium_gain, preview=True
        )
        await update.message.reply_html(text)

    async def cmd_liquidity_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if self.uw is None:
            await update.message.reply_text("UW غير متصل.")
            return
        from .uw_client import UWError
        try:
            response = await self.uw.get("/api/option-trades", {
                "ticker_symbol": "SPX", "min_dte": 0, "max_dte": 0, "limit": 1,
            })
        except UWError as error:
            await update.message.reply_text(
                f"تعذر التحقق من بيانات التدفقات: {error.kind}. "
                "لا يُفعّل اختيار السيولة قبل التأكد من صلاحية هذا المصدر في اشتراك UW."
            )
            return
        await update.message.reply_text(
            "✅ مصدر التدفقات متاح للاشتراك. "
            "يظل فحص اكتمال وحداثة البيانات مطلوبًا أثناء الجلسة."
        )

    async def cmd_radar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if self.liquidity is None:
            await update.message.reply_text("بيانات UW غير متاحة.")
            return
        now = utcnow()
        from .timeutil import within_session
        if to_et(now).weekday() >= 5 or not within_session(now, self.settings.market.open_et, self.settings.market.close_et):
            await update.message.reply_text("المعاينة الحية متاحة أثناء جلسة السوق فقط؛ لا نعرض جلسة قديمة كقراءة اليوم.")
            return
        opened = et_datetime(et_date(now), self.settings.market.open_et)
        end = now.replace(second=0, microsecond=0)
        start = max(opened, end - timedelta(hours=1))
        snapshot = await self.liquidity.radar_snapshot(start, end)
        observed_at = utcnow()
        if not snapshot.fresh(observed_at, max_age=180):
            await update.message.reply_text("بانتظار تدفقات مكتملة وحديثة. " + (snapshot.reason or "NO_FRESH_DATA"))
            return
        await update.message.reply_html(radar_text(snapshot, observed_at, start == opened))

    async def cmd_highest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if not self.settings.flags.highest_card:
            await update.message.reply_text("HIGHEST_CARD_ENABLED غير مفعّل.")
            return
        if len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("الاستخدام: /qa_highest <trade_id>")
            return
        trade = await self.db.fetchrow(q.GET_TRADE, int(context.args[0]))
        if trade is None:
            await update.message.reply_text("لا توجد صفقة بهذا الرقم.")
            return
        sent = await self.tracker.send_highest_card(trade)
        if sent:
            await update.message.reply_text("✅ أُرسلت بطاقة Highest Price إلى القناة.")
        else:
            await update.message.reply_text(
                "لم تُرسل: إما أُرسلت سابقًا، أو الصفقة لم تحقق +$50، "
                "أو أعلى Observed Bid ليس أعلى من الدخول."
            )


    async def cmd_reconcile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        args = context.args or []
        if len(args) != 2 or not args[0].isdigit() or args[1] not in ("sent", "resend"):
            await update.message.reply_text(
                "الاستخدام: /qa_reconcile <event_id> sent|resend"
            )
            return
        row = await self.tracker.reconcile_event(int(args[0]), args[1])
        if row is None:
            await update.message.reply_text(
                "لا يوجد حدث بهذا الرقم بحالة NEEDS_RECONCILIATION."
            )
            return
        await update.message.reply_text(
            f"✅ Event #{row['id']} → {row['status']}"
            + (" (سيُعاد إرساله في الـPoll التالي)" if row["status"] == "FAILED" else "")
        )


def setup_automation(application: Application, renderers: CardRenderers) -> Optional[AutomationRuntime]:
    """Register automation hooks/handlers. Never raises."""
    try:
        settings = load_settings()
    except Exception:
        logger.exception("Automation settings invalid — manual bot only")
        return None

    runtime = AutomationRuntime(application, settings, renderers)

    previous_init = application.post_init
    previous_shutdown = application.post_shutdown

    async def post_init(app: Application) -> None:
        if previous_init is not None:
            await previous_init(app)
        await runtime.start(app)

    async def post_shutdown(app: Application) -> None:
        await runtime.stop(app)
        if previous_shutdown is not None:
            await previous_shutdown(app)

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    application.add_handler(
        CallbackQueryHandler(runtime.approval.on_callback, pattern=r"^qa:")
    )
    application.add_handler(CommandHandler("qa_track", runtime.manual.command))
    application.add_handler(CallbackQueryHandler(runtime.manual.callback, pattern=r"^qam:"))
    application.add_handler(CommandHandler("qa_status", runtime.cmd_status))
    application.add_handler(CommandHandler("qa_close", runtime.cmd_close))
    application.add_handler(CommandHandler("qa_daily", runtime.cmd_daily))
    application.add_handler(CommandHandler("qa_radar", runtime.cmd_radar))
    application.add_handler(CommandHandler("qa_liquidity_check", runtime.cmd_liquidity_check))
    application.add_handler(CommandHandler("qa_highest", runtime.cmd_highest))
    application.add_handler(CommandHandler("qa_reconcile", runtime.cmd_reconcile))
    application.bot_data["quiet_alpha_runtime"] = runtime
    logger.info("Automation hooks registered")
    return runtime


__all__ = ["setup_automation", "AutomationRuntime", "Decimal", "decision_footer", "utcnow"]

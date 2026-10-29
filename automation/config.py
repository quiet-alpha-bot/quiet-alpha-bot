"""Configuration for the Quiet Alpha automation layer.

Three kinds of settings live here:

1. Secrets and IDs (environment only, never logged):
   BOT_TOKEN, SIGNAL_CHAT_ID, OWNER_USER_ID, UW_API_KEY,
   TV_WEBHOOK_SECRET, DATABASE_URL.

2. Feature flags. Every flag defaults to false. Building a component does
   not enable it.

3. Strategy settings. Values that have NOT been decided yet are marked
   "DEVELOPMENT / SHADOW DEFAULT". They exist only so the code can run;
   they are NOT final trading rules. Every one of them can be overridden
   with an environment variable of the same name, without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal, InvalidOperation
from typing import Optional


# =========================================================
# Environment helpers
# =========================================================

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value if value != "" else None


def env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"{name} must be true/false")


def env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = _env(name)
    if raw is None or raw.lower() == "none":
        return default if raw is None else None
    return int(raw)


def env_decimal(name: str, default: Optional[str]) -> Optional[Decimal]:
    raw = _env(name)
    if raw is None:
        return Decimal(default) if default is not None else None
    if raw.lower() == "none":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a number") from error


def env_str(name: str, default: Optional[str]) -> Optional[str]:
    raw = _env(name)
    if raw is None:
        return default
    if raw.lower() == "none":
        return None
    return raw


def env_time(name: str, default: Optional[str]) -> Optional[time]:
    raw = env_str(name, default)
    if raw is None:
        return None
    hours, minutes = raw.split(":")
    return time(int(hours), int(minutes))


def parse_owner_user_id(raw: Optional[str]) -> Optional[int]:
    """Return a positive Telegram user id, or None if missing/invalid.

    A Telegram *user* id is always a positive integer. Negative ids belong
    to groups/channels and are rejected here on purpose: a chat id must
    never be used as an owner id.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


def automation_requested() -> bool:
    """Master switch, read without building the full config."""
    try:
        return env_bool("AUTOMATION_ENABLED", False)
    except ValueError:
        return False


# =========================================================
# Settings
# =========================================================


@dataclass(frozen=True)
class Flags:
    tv_intake: bool
    uw_watch: bool
    uw_watch_notify: bool
    matching: bool
    selection: bool
    proposals: bool
    tracking: bool
    milestones: bool
    success_card: bool
    highest_card: bool
    stop_loss_card: bool
    liquidity_radar: bool = False
    flow_selection: bool = False
    breakeven_exit: bool = False

    @classmethod
    def from_env(cls) -> "Flags":
        return cls(
            tv_intake=env_bool("TV_INTAKE_ENABLED"),
            uw_watch=env_bool("UW_WATCH_ENABLED"),
            uw_watch_notify=env_bool("UW_WATCH_NOTIFY"),
            matching=env_bool("MATCHING_ENABLED"),
            selection=env_bool("SELECTION_ENABLED"),
            proposals=env_bool("PROPOSALS_ENABLED"),
            tracking=env_bool("TRACKING_ENABLED"),
            milestones=env_bool("MILESTONES_ENABLED"),
            success_card=env_bool("SUCCESS_CARD_ENABLED"),
            highest_card=env_bool("HIGHEST_CARD_ENABLED"),
            stop_loss_card=env_bool("STOP_LOSS_CARD_ENABLED"),
            liquidity_radar=env_bool("LIQUIDITY_RADAR_ENABLED"),
            flow_selection=env_bool("FLOW_SELECTION_ENABLED"),
            breakeven_exit=env_bool("BREAKEVEN_EXIT_ENABLED"),
        )


@dataclass(frozen=True)
class UWSettings:
    base_url: str
    timeout_seconds: float
    max_retries: int
    # DEVELOPMENT / SHADOW DEFAULT: which ticker path returns SPXW rows is
    # verified by tools/uw_basic_probe.py. Tried in order.
    chain_tickers: tuple[str, ...]
    chain_max_pages: int
    contract_root: str
    spot_ticker: str


@dataclass(frozen=True)
class WatchSettings:
    ticker: str
    # DEVELOPMENT / SHADOW DEFAULTS — not final trading rules.
    poll_seconds: int
    window_minutes: int
    min_net_premium: Decimal
    min_strength: Decimal
    cooldown_minutes: int
    use_flow_alerts: bool
    flow_alerts_limit: int


@dataclass(frozen=True)
class MatchSettings:
    # DEVELOPMENT / SHADOW DEFAULTS — not final trading rules.
    window_minutes: int
    mixed_policy: str  # "CONFLICT" or "LATEST"
    expiry_grace_seconds: int
    rules_version: str


@dataclass(frozen=True)
class SelectionSettings:
    # Approved preferred premium range.
    premium_min: Decimal
    premium_max: Decimal
    # Hard filters left disabled (None) until real data exists.
    max_spread_abs: Optional[Decimal]
    max_spread_pct: Optional[Decimal]
    min_volume: Optional[int]
    min_open_interest: Optional[int]
    delta_min_abs: Optional[Decimal]
    delta_max_abs: Optional[Decimal]
    max_tape_age_seconds: Optional[int]
    require_positive_bid: bool
    # DEVELOPMENT / SHADOW DEFAULT ranking weights.
    weight_spread: Decimal
    weight_liquidity: Decimal
    weight_centrality: Decimal
    weight_delta: Decimal
    weight_freshness: Decimal
    weight_distance: Decimal
    delta_target_abs: Decimal
    top_n_reported: int
    flow_window_minutes: int = 10
    flow_max_pages: int = 20
    alternative_attempts: int = 2
    momentum_max_age_seconds: int = 300


@dataclass(frozen=True)
class EntrySettings:
    # DEVELOPMENT / SHADOW DEFAULTS — not final trading rules.
    window_seconds: int
    poll_seconds: int
    wait_spread_pct: Optional[Decimal]
    stability_max_move: Optional[Decimal]
    chase_limit_pct: Optional[Decimal]
    on_window_end: str  # "ENTER" or "SKIP"
    max_signal_age_seconds: Optional[int]
    last_entry_time_et: Optional[time]


@dataclass(frozen=True)
class ApprovalSettings:
    # DEVELOPMENT / SHADOW DEFAULTS — not final trading rules.
    tolerance_abs: Optional[Decimal]
    tolerance_pct: Optional[Decimal]
    proposal_expiry_seconds: Optional[int]
    require_in_range: bool
    confirmation_tolerance_abs: Decimal = Decimal("0.30")


@dataclass(frozen=True)
class TrackingSettings:
    # DEVELOPMENT / SHADOW DEFAULT polling. Final value comes from the probe.
    poll_seconds: int
    message_refresh_seconds: int
    milestone_step_pct: int
    # Approved rule: SUCCESS = approved_entry + $0.50 (+$50 gross/contract).
    success_premium_gain: Decimal
    # Before SUCCESS: close at entry minus 50%.
    stop_loss_pct: Decimal
    # After SUCCESS: protect capital at entry minus $0.20 premium.
    protected_stop_buffer: Decimal
    stop_time_et: time
    # OPEN DECISION: "none" (default) or "on_close".
    highest_card_trigger: str
    # A ledger event left PENDING longer than this (crash/redeploy during a
    # send) is moved to NEEDS_RECONCILIATION. Must exceed the longest send
    # (photo timeouts total 240s), so the default 600s never races a live send.
    pending_stale_seconds: int = 600
    breakeven_drawdown_pct: Decimal = Decimal("0.35")
    group_milestones: bool = True


@dataclass(frozen=True)
class MarketSettings:
    open_et: time
    close_et: time
    display_timezone: str


@dataclass(frozen=True)
class Settings:
    bot_token: Optional[str]
    signal_chat_id: Optional[str]
    owner_user_id: Optional[int]
    uw_api_key: Optional[str] = field(repr=False, default=None)
    tv_webhook_secret: Optional[str] = field(repr=False, default=None)
    database_url: Optional[str] = field(repr=False, default=None)
    port: int = 8080
    webhook_path: str = "/webhook/tradingview"
    webhook_max_body_bytes: int = 16384
    notify_signal_status: bool = True
    flags: Flags = None  # type: ignore[assignment]
    uw: UWSettings = None  # type: ignore[assignment]
    watch: WatchSettings = None  # type: ignore[assignment]
    match: MatchSettings = None  # type: ignore[assignment]
    selection: SelectionSettings = None  # type: ignore[assignment]
    entry: EntrySettings = None  # type: ignore[assignment]
    approval: ApprovalSettings = None  # type: ignore[assignment]
    tracking: TrackingSettings = None  # type: ignore[assignment]
    market: MarketSettings = None  # type: ignore[assignment]

    def __repr__(self) -> str:  # never print secrets
        return "Settings(<redacted>)"

    def problems(self) -> list[str]:
        """Human-readable configuration problems (no secret values)."""
        issues: list[str] = []
        if not self.bot_token:
            issues.append("BOT_TOKEN is missing")
        if not self.signal_chat_id:
            issues.append("SIGNAL_CHAT_ID is missing")
        if self.owner_user_id is None:
            issues.append(
                "OWNER_USER_ID is missing or invalid "
                "(must be a positive Telegram user id)"
            )
        if not self.database_url:
            issues.append("DATABASE_URL is missing")
        uw_needed = (
            self.flags.uw_watch
            or self.flags.selection
            or self.flags.tracking
            or self.flags.liquidity_radar
            or self.flags.flow_selection
        )
        if uw_needed and not self.uw_api_key:
            issues.append("UW_API_KEY is missing")
        if self.flags.tv_intake and not self.tv_webhook_secret:
            issues.append("TV_WEBHOOK_SECRET is missing")
        if self.match.mixed_policy not in {"CONFLICT", "LATEST"}:
            issues.append("MATCH_MIXED_POLICY must be CONFLICT or LATEST")
        if self.entry.on_window_end not in {"ENTER", "SKIP"}:
            issues.append("ENTRY_ON_WINDOW_END must be ENTER or SKIP")
        if self.tracking.highest_card_trigger not in {"none", "on_close"}:
            issues.append("HIGHEST_CARD_TRIGGER must be none or on_close")
        if self.selection.premium_min >= self.selection.premium_max:
            issues.append("PREMIUM_MIN must be below PREMIUM_MAX")
        if self.tracking.milestone_step_pct <= 0:
            issues.append("MILESTONE_STEP_PCT must be positive")
        if not Decimal("0") < self.tracking.stop_loss_pct < Decimal("1"):
            issues.append("STOP_LOSS_PCT must be greater than 0 and below 1")
        if self.tracking.protected_stop_buffer < Decimal("0"):
            issues.append("PROTECTED_STOP_BUFFER must not be negative")
        return issues


def load_settings() -> Settings:
    flags = Flags.from_env()

    uw = UWSettings(
        base_url=env_str("UW_BASE_URL", "https://api.unusualwhales.com"),
        timeout_seconds=float(env_str("UW_TIMEOUT_SECONDS", "10")),
        max_retries=env_int("UW_MAX_RETRIES", 2) or 0,
        chain_tickers=tuple(
            item.strip()
            for item in env_str("UW_CHAIN_TICKERS", "SPX,SPXW").split(",")
            if item.strip()
        ),
        chain_max_pages=env_int("UW_CHAIN_MAX_PAGES", 4) or 1,
        contract_root=env_str("UW_CONTRACT_ROOT", "SPXW"),
        spot_ticker=env_str("UW_SPOT_TICKER", "SPX"),
    )

    watch = WatchSettings(
        ticker=env_str("UW_WATCH_TICKER", "SPX"),
        poll_seconds=env_int("UW_WATCH_POLL_SECONDS", 60) or 60,
        window_minutes=env_int("UW_WATCH_WINDOW_MINUTES", 5) or 5,
        min_net_premium=env_decimal("UW_WATCH_MIN_NET_PREMIUM", "1000000"),
        min_strength=env_decimal("UW_WATCH_MIN_STRENGTH", "0.30"),
        cooldown_minutes=env_int("UW_WATCH_COOLDOWN_MINUTES", 15) or 15,
        use_flow_alerts=env_bool("UW_WATCH_USE_FLOW_ALERTS", True),
        flow_alerts_limit=env_int("UW_WATCH_FLOW_ALERTS_LIMIT", 100) or 100,
    )

    match = MatchSettings(
        window_minutes=env_int("MATCH_WINDOW_MINUTES", 30) or 30,
        mixed_policy=(env_str("MATCH_MIXED_POLICY", "CONFLICT") or "").upper(),
        expiry_grace_seconds=env_int("UW_WATCH_EXPIRY_GRACE_SECONDS", 60) or 0,
        rules_version=env_str("MATCH_RULES_VERSION", "match-v1-dev"),
    )

    selection = SelectionSettings(
        premium_min=env_decimal("PREMIUM_MIN", "1.00"),
        premium_max=env_decimal("PREMIUM_MAX", "4.50"),
        max_spread_abs=env_decimal("MAX_SPREAD_ABS", None),
        max_spread_pct=env_decimal("MAX_SPREAD_PCT", None),
        min_volume=env_int("MIN_VOLUME", None),
        min_open_interest=env_int("MIN_OPEN_INTEREST", None),
        delta_min_abs=env_decimal("DELTA_MIN_ABS", None),
        delta_max_abs=env_decimal("DELTA_MAX_ABS", None),
        max_tape_age_seconds=env_int("MAX_TAPE_AGE_SECONDS", None),
        require_positive_bid=env_bool("REQUIRE_POSITIVE_BID", True),
        weight_spread=env_decimal("W_SPREAD", "0.40"),
        weight_liquidity=env_decimal("W_LIQUIDITY", "0.25"),
        weight_centrality=env_decimal("W_CENTRALITY", "0.15"),
        weight_delta=env_decimal("W_DELTA", "0.10"),
        weight_freshness=env_decimal("W_FRESHNESS", "0.10"),
        weight_distance=env_decimal("W_DISTANCE", "0"),
        delta_target_abs=env_decimal("DELTA_TARGET_ABS", "0.30"),
        top_n_reported=env_int("TOP_N_REPORTED", 3) or 3,
        flow_window_minutes=max(1, env_int("FLOW_WINDOW_MINUTES", 10) or 10),
        flow_max_pages=max(1, env_int("FLOW_MAX_PAGES", 20) or 20),
        alternative_attempts=max(0, env_int("ALTERNATIVE_ATTEMPTS", 2) or 0),
        momentum_max_age_seconds=max(30, env_int("MOMENTUM_MAX_AGE_SECONDS", 300) or 300),
    )

    entry = EntrySettings(
        window_seconds=env_int("ENTRY_WINDOW_SECONDS", 30) or 0,
        poll_seconds=env_int("ENTRY_POLL_SECONDS", 10) or 10,
        wait_spread_pct=env_decimal("ENTRY_WAIT_SPREAD_PCT", "0.10"),
        stability_max_move=env_decimal("ENTRY_STABILITY_MAX_MOVE", "0.10"),
        chase_limit_pct=env_decimal("ENTRY_CHASE_LIMIT_PCT", "0.15"),
        on_window_end=(env_str("ENTRY_ON_WINDOW_END", "ENTER") or "").upper(),
        max_signal_age_seconds=env_int("MAX_SIGNAL_AGE_SECONDS", 300),
        last_entry_time_et=env_time("LAST_ENTRY_TIME_ET", "15:45"),
    )

    approval = ApprovalSettings(
        tolerance_abs=env_decimal("ENTRY_TOLERANCE_ABS", "0.10"),
        tolerance_pct=env_decimal("ENTRY_TOLERANCE_PCT", None),
        proposal_expiry_seconds=env_int("PROPOSAL_EXPIRY_SECONDS", 300),
        require_in_range=env_bool("APPROVAL_REQUIRE_IN_RANGE", True),
    )

    tracking = TrackingSettings(
        group_milestones=env_bool("MILESTONES_GROUPED", True),
        poll_seconds=env_int("TRACKING_POLL_SECONDS", 1) or 1,
        message_refresh_seconds=env_int("TRACKING_MESSAGE_REFRESH_SECONDS", 60) or 0,
        milestone_step_pct=env_int("MILESTONE_STEP_PCT", 10) or 10,
        success_premium_gain=env_decimal("SUCCESS_PREMIUM_GAIN", "0.50"),
        stop_loss_pct=env_decimal("STOP_LOSS_PCT", "0.50"),
        protected_stop_buffer=env_decimal("PROTECTED_STOP_BUFFER", "0.20"),
        stop_time_et=env_time("TRACKING_STOP_TIME_ET", "16:00"),
        highest_card_trigger=(env_str("HIGHEST_CARD_TRIGGER", "none") or "none").lower(),
        pending_stale_seconds=max(300, env_int("EVENT_PENDING_STALE_SECONDS", 600) or 600),
    )

    market = MarketSettings(
        open_et=env_time("MARKET_OPEN_ET", "09:30"),
        close_et=env_time("MARKET_CLOSE_ET", "16:00"),
        display_timezone=env_str("DISPLAY_TIMEZONE", "Asia/Riyadh"),
    )

    return Settings(
        bot_token=_env("BOT_TOKEN"),
        signal_chat_id=_env("SIGNAL_CHAT_ID"),
        owner_user_id=parse_owner_user_id(_env("OWNER_USER_ID")),
        uw_api_key=_env("UW_API_KEY"),
        tv_webhook_secret=_env("TV_WEBHOOK_SECRET"),
        database_url=_env("DATABASE_URL"),
        port=env_int("PORT", 8080) or 8080,
        webhook_path=env_str("WEBHOOK_PATH", "/webhook/tradingview"),
        webhook_max_body_bytes=env_int("WEBHOOK_MAX_BODY_BYTES", 16384) or 16384,
        notify_signal_status=env_bool("NOTIFY_SIGNAL_STATUS", True),
        flags=flags,
        uw=uw,
        watch=watch,
        match=match,
        selection=selection,
        entry=entry,
        approval=approval,
        tracking=tracking,
        market=market,
    )


def strategy_snapshot(settings: Settings) -> dict:
    """Non-secret settings, stored with decisions for later analysis."""

    def plain(obj) -> dict:
        out = {}
        for key, value in obj.__dict__.items():
            if isinstance(value, (Decimal, time)):
                out[key] = str(value)
            elif isinstance(value, tuple):
                out[key] = list(value)
            else:
                out[key] = value
        return out

    return {
        "watch": plain(settings.watch),
        "match": plain(settings.match),
        "selection": plain(settings.selection),
        "entry": plain(settings.entry),
        "approval": plain(settings.approval),
        "tracking": plain(settings.tracking),
    }

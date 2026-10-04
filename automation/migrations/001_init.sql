-- Quiet Alpha automation schema v1.
-- Idempotent: safe to run on every startup (guarded by schema_migrations).

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------
-- Indicator signals (TradingView)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL DEFAULT 'tradingview',
    direction       TEXT NOT NULL CHECK (direction IN ('CALL', 'PUT')),
    event_type      TEXT,
    ticker          TEXT,
    timeframe       TEXT,
    tv_price        NUMERIC,
    signal_time     TIMESTAMPTZ NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'RECEIVED',
    status_reason   TEXT,
    classification  TEXT,
    processing_started_at TIMESTAMPTZ,
    processed_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS signals_time_idx ON signals (signal_time);
CREATE INDEX IF NOT EXISTS signals_status_idx ON signals (status);

-- ---------------------------------------------------------
-- UW watch engine
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS uw_watch_observations (
    id              BIGSERIAL PRIMARY KEY,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker          TEXT NOT NULL,
    window_start    TIMESTAMPTZ,
    window_end      TIMESTAMPTZ,
    net_premium     NUMERIC,
    strength        NUMERIC,
    direction       TEXT,
    triggered       BOOLEAN NOT NULL DEFAULT FALSE,
    metrics         JSONB NOT NULL,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS uw_obs_time_idx ON uw_watch_observations (observed_at);

CREATE TABLE IF NOT EXISTS uw_watches (
    id              BIGSERIAL PRIMARY KEY,
    watch_key       TEXT NOT NULL UNIQUE,
    direction       TEXT NOT NULL CHECK (direction IN ('CALL', 'PUT')),
    watch_time      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    net_premium     NUMERIC,
    strength        NUMERIC,
    context         JSONB NOT NULL,
    observation_id  BIGINT REFERENCES uw_watch_observations (id),
    status          TEXT NOT NULL DEFAULT 'INDICATOR_PENDING',
    status_changed_at TIMESTAMPTZ,
    rules           JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS uw_watches_time_idx ON uw_watches (watch_time);
CREATE INDEX IF NOT EXISTS uw_watches_status_idx ON uw_watches (status);

-- ---------------------------------------------------------
-- Classification records (immutable once written)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_matches (
    id                      BIGSERIAL PRIMARY KEY,
    classification          TEXT NOT NULL,
    signal_db_id            BIGINT REFERENCES signals (id),
    watch_id                BIGINT REFERENCES uw_watches (id),
    indicator_direction     TEXT,
    uw_direction            TEXT,
    indicator_signal_time   TIMESTAMPTZ,
    uw_watch_time           TIMESTAMPTZ,
    time_difference_seconds NUMERIC,
    related_watch_ids       BIGINT[] NOT NULL DEFAULT '{}',
    watch_engine_active     BOOLEAN,
    rules_version           TEXT NOT NULL,
    window_minutes          INTEGER NOT NULL,
    classified_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One primary classification per signal.
CREATE UNIQUE INDEX IF NOT EXISTS signal_matches_signal_uq
    ON signal_matches (signal_db_id)
    WHERE classification IN ('INDICATOR_ONLY', 'BOTH_CONFIRMED', 'CONFLICT');
-- One UW_ONLY record per watch.
CREATE UNIQUE INDEX IF NOT EXISTS signal_matches_uw_only_uq
    ON signal_matches (watch_id)
    WHERE classification = 'UW_ONLY';
-- One late observation per (signal, watch).
CREATE UNIQUE INDEX IF NOT EXISTS signal_matches_late_uq
    ON signal_matches (signal_db_id, watch_id)
    WHERE classification IN ('LATE_UW_AGREEMENT', 'LATE_UW_DISAGREEMENT');

-- ---------------------------------------------------------
-- Contract selection + entry decisions
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS selection_runs (
    id              BIGSERIAL PRIMARY KEY,
    signal_db_id    BIGINT NOT NULL REFERENCES signals (id),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    chain_ticker    TEXT,
    expiry          DATE,
    spot            NUMERIC,
    candidates_total INTEGER,
    candidates_passed INTEGER,
    selected_symbol TEXT,
    status          TEXT NOT NULL DEFAULT 'RUNNING',
    reason          TEXT,
    settings        JSONB NOT NULL,
    meta            JSONB
);
CREATE INDEX IF NOT EXISTS selection_runs_signal_idx ON selection_runs (signal_db_id);

CREATE TABLE IF NOT EXISTS contract_candidates (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES selection_runs (id),
    option_symbol   TEXT NOT NULL,
    strike          NUMERIC,
    expiry          DATE,
    option_type     TEXT,
    bid             NUMERIC,
    ask             NUMERIC,
    last            NUMERIC,
    mid             NUMERIC,
    spread          NUMERIC,
    spread_pct      NUMERIC,
    volume          BIGINT,
    open_interest   BIGINT,
    delta           NUMERIC,
    last_tape_time  TIMESTAMPTZ,
    passed          BOOLEAN NOT NULL,
    reject_reasons  TEXT[] NOT NULL DEFAULT '{}',
    score           NUMERIC,
    score_breakdown JSONB,
    rank            INTEGER
);
CREATE INDEX IF NOT EXISTS contract_candidates_run_idx ON contract_candidates (run_id);

CREATE TABLE IF NOT EXISTS entry_decisions (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES selection_runs (id),
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision        TEXT NOT NULL,
    reason          TEXT NOT NULL,
    bid             NUMERIC,
    ask             NUMERIC,
    spread_pct      NUMERIC,
    signal_age_seconds NUMERIC,
    inputs          JSONB
);
CREATE INDEX IF NOT EXISTS entry_decisions_run_idx ON entry_decisions (run_id);

-- ---------------------------------------------------------
-- Proposals (one per signal)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS proposals (
    id              BIGSERIAL PRIMARY KEY,
    signal_db_id    BIGINT NOT NULL UNIQUE REFERENCES signals (id),
    run_id          BIGINT REFERENCES selection_runs (id),
    direction       TEXT NOT NULL,
    option_symbol   TEXT NOT NULL,
    strike          NUMERIC NOT NULL,
    expiry          DATE NOT NULL,
    suggested_entry NUMERIC NOT NULL,
    bid             NUMERIC,
    ask             NUMERIC,
    last            NUMERIC,
    classification  TEXT,
    uw_watch_id     BIGINT REFERENCES uw_watches (id),
    details         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
    approving_from  TEXT,
    approving_since TIMESTAMPTZ,
    reconfirm_price NUMERIC,
    reconfirm_quote JSONB,
    message_id      BIGINT,
    reconfirm_message_id BIGINT,
    expires_at      TIMESTAMPTZ,
    decided_at      TIMESTAMPTZ,
    decided_by      BIGINT,
    decision_note   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS proposals_status_idx ON proposals (status);

-- ---------------------------------------------------------
-- Trades (locked after final approval)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id                  BIGSERIAL PRIMARY KEY,
    proposal_id         BIGINT NOT NULL UNIQUE REFERENCES proposals (id),
    signal_db_id        BIGINT NOT NULL REFERENCES signals (id),
    signal_id           TEXT NOT NULL,
    option_symbol       TEXT NOT NULL,
    direction           TEXT NOT NULL,
    strike              NUMERIC NOT NULL,
    expiry              DATE NOT NULL,
    suggested_entry     NUMERIC NOT NULL,
    approved_entry      NUMERIC NOT NULL,
    approval_quote      JSONB,
    classification      TEXT,
    uw_watch_id         BIGINT REFERENCES uw_watches (id),
    signal_time         TIMESTAMPTZ NOT NULL,
    approval_time       TIMESTAMPTZ NOT NULL,
    status              TEXT NOT NULL DEFAULT 'TRACKING',
    close_reason        TEXT,
    closed_at           TIMESTAMPTZ,
    current_bid         NUMERIC,
    current_ask         NUMERIC,
    current_last        NUMERIC,
    highest_bid         NUMERIC,
    highest_bid_time    TIMESTAMPTZ,
    highest_mid         NUMERIC,
    highest_last        NUMERIC,
    lowest_bid          NUMERIC,
    lowest_bid_time     TIMESTAMPTZ,
    last_poll_at        TIMESTAMPTZ,
    last_tape_time      TIMESTAMPTZ,
    last_error          TEXT,
    highest_milestone_sent INTEGER NOT NULL DEFAULT 0,
    success_detected_at TIMESTAMPTZ,
    success_bid         NUMERIC,
    success_sent        BOOLEAN NOT NULL DEFAULT FALSE,
    tracking_message_id BIGINT,
    tracking_message_edited_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trades_status_idx ON trades (status);

CREATE TABLE IF NOT EXISTS price_observations (
    id              BIGSERIAL PRIMARY KEY,
    trade_id        BIGINT NOT NULL REFERENCES trades (id),
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    bid             NUMERIC,
    ask             NUMERIC,
    last            NUMERIC,
    volume          BIGINT,
    open_interest   BIGINT,
    last_tape_time  TIMESTAMPTZ,
    latency_ms      INTEGER,
    changed         BOOLEAN,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS price_obs_trade_idx ON price_observations (trade_id, observed_at);

-- ---------------------------------------------------------
-- Event ledger: one row per Telegram output / milestone. SENT rows are never
-- republished automatically; exactly-once is NOT guaranteed if the process
-- dies after Telegram accepted a message but before SENT was recorded
-- (such rows go to NEEDS_RECONCILIATION, not blind resend).
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id              BIGSERIAL PRIMARY KEY,
    entity_type     TEXT NOT NULL,      -- 'trade' | 'proposal' | 'watch' | 'signal'
    entity_id       BIGINT NOT NULL,
    event_type      TEXT NOT NULL,      -- e.g. ENTRY_CARD, MILESTONE, SUCCESS_CARD
    level           INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|SENT|FAILED|UNCERTAIN|RECORDED
    attempts        INTEGER NOT NULL DEFAULT 0,
    message_id      BIGINT,
    payload         JSONB,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_id, event_type, level)
);

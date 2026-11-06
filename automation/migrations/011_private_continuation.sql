-- One durable search and at most one child per original trade.
CREATE TABLE IF NOT EXISTS private_continuations (
    origin_trade_id BIGINT PRIMARY KEY REFERENCES trades(id),
    status TEXT NOT NULL DEFAULT 'SEARCHING' CHECK (status IN ('SEARCHING','OPENED','SKIPPED')),
    started_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    child_trade_id BIGINT UNIQUE REFERENCES trades(id),
    reason TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'
);
-- Freeze multipart report contents before the first Telegram send.
CREATE TABLE IF NOT EXISTS report_snapshots (
    entity_type TEXT NOT NULL, entity_id BIGINT NOT NULL, event_type TEXT NOT NULL,
    body TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(entity_type,entity_id,event_type)
);

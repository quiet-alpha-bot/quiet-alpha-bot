-- Immutable alert evidence and restart-safe per-contract escalation history.
CREATE TABLE IF NOT EXISTS company_radar_alerts (
    id BIGSERIAL PRIMARY KEY,
    market_date DATE NOT NULL,
    option_symbol TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level >= 0),
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    premium NUMERIC NOT NULL CHECK (premium >= 1000000),
    message_text TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE (market_date, option_symbol, level),
    CHECK (window_end > window_start)
);

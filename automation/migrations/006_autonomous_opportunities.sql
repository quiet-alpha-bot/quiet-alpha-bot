-- Isolated from closed-trade accounting; trusted internal triggers only.
CREATE TABLE IF NOT EXISTS spx_radar_alerts (
 id BIGSERIAL PRIMARY KEY, market_date DATE NOT NULL, option_symbol TEXT NOT NULL,
 level INTEGER NOT NULL CHECK(level>=0), window_start TIMESTAMPTZ NOT NULL,
 window_end TIMESTAMPTZ NOT NULL, premium NUMERIC NOT NULL CHECK(premium>=1000000),
 message_text TEXT NOT NULL, payload JSONB NOT NULL,
 UNIQUE(market_date,option_symbol,level), CHECK(window_end>window_start)
);
CREATE TABLE IF NOT EXISTS opportunity_quotes (
 option_symbol TEXT PRIMARY KEY, bid NUMERIC NOT NULL, tape_at TIMESTAMPTZ NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL, previous_bid NUMERIC,
 previous_tape_at TIMESTAMPTZ, previous_observed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS autonomous_triggers (
 id BIGSERIAL PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('REENTRY','SPX_RADAR')),
 option_symbol TEXT NOT NULL, origin_trade_id BIGINT REFERENCES trades(id),
 alert_id BIGINT REFERENCES spx_radar_alerts(id), reference_entry NUMERIC,
 created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
 signal_db_id BIGINT UNIQUE REFERENCES signals(id),
 CHECK(expires_at>created_at),
 CHECK((kind='REENTRY' AND origin_trade_id IS NOT NULL AND reference_entry>0)
    OR (kind='SPX_RADAR' AND alert_id IS NOT NULL))
);

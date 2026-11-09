CREATE TABLE IF NOT EXISTS company_positions (
 id BIGSERIAL PRIMARY KEY,
 alert_id BIGINT NOT NULL UNIQUE REFERENCES company_radar_alerts(id),
 option_symbol TEXT NOT NULL,
 expiry DATE NOT NULL,
 entry_day DATE NOT NULL,
 entry_price NUMERIC NOT NULL CHECK(entry_price>0),
 entry_quote_at TIMESTAMPTZ NOT NULL,
 entry_text TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','OPEN','CLOSED','CANCELLED')),
 published_at TIMESTAMPTZ,
 current_bid NUMERIC, highest_bid NUMERIC, lowest_bid NUMERIC,
 last_quote_at TIMESTAMPTZ,
 success_at TIMESTAMPTZ,
 closed_at TIMESTAMPTZ, close_reason TEXT,
 live_bid NUMERIC,
 UNIQUE(option_symbol,entry_day)
);
CREATE UNIQUE INDEX IF NOT EXISTS company_one_active_symbol ON company_positions(option_symbol)
 WHERE status IN ('PENDING','OPEN');
CREATE TABLE IF NOT EXISTS company_session_marks (
 position_id BIGINT NOT NULL REFERENCES company_positions(id),
 market_date DATE NOT NULL,
 current_bid NUMERIC, highest_bid NUMERIC, lowest_bid NUMERIC,
 last_quote_at TIMESTAMPTZ, success_at TIMESTAMPTZ,
 status TEXT NOT NULL, close_reason TEXT,
 PRIMARY KEY(position_id,market_date)
);

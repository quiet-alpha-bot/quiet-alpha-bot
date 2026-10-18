-- Freeze report content before delivery so a retry cannot change its window.
CREATE TABLE IF NOT EXISTS liquidity_radar_reports (
    market_date DATE NOT NULL,
    slot INTEGER NOT NULL CHECK (slot >= 0),
    message_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (market_date, slot)
);

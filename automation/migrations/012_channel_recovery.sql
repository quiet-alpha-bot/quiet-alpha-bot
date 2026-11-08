-- Channel-specific stop snapshot and durable recovery arming. Never backfill old trades.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS private_close JSONB;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS recovered_at TIMESTAMPTZ;

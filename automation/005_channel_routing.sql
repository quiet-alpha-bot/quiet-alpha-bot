-- NULL preserves the original admin-only destination of existing trades.
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS publication_chat_id TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS publication_chat_id TEXT;

-- Milestones are DETECTED first (persisted), then SENT in threshold order.
-- After a restart, detected-but-unsent events are completed, never repeated.
ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS highest_milestone_reached INTEGER NOT NULL DEFAULT 0;
ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS milestone_detections JSONB NOT NULL DEFAULT '{}'::jsonb;

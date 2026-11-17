ALTER TABLE trades ADD COLUMN IF NOT EXISTS deleted_channels JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE TABLE IF NOT EXISTS execution_control (
 id INTEGER PRIMARY KEY CHECK(id=1), paused BOOLEAN NOT NULL DEFAULT TRUE,
 resumed_at TIMESTAMPTZ, changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 revision BIGINT NOT NULL DEFAULT 0
);
INSERT INTO execution_control(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE SCHEMA IF NOT EXISTS qa_execution_feed;
CREATE OR REPLACE VIEW qa_execution_feed.control AS SELECT paused,resumed_at,changed_at,revision FROM execution_control WHERE id=1;
CREATE OR REPLACE VIEW qa_execution_feed.signals WITH (security_barrier=true) AS
SELECT t.id,t.option_symbol,t.expiry,t.strike,t.direction,t.approved_entry,t.status,
 jsonb_build_object('trade_mode',coalesce(t.approval_quote->>'trade_mode','NORMAL')) AS approval_quote,
 e.id AS publication_event_id,e.event_type,e.status AS delivery_status,e.message_id,
 jsonb_build_object('publication_chat_id',e.payload->>'publication_chat_id','mode',e.payload->>'mode') AS publication_payload,
 (e.payload->>'telegram_sent_at')::timestamptz AS publication_time
FROM trades t JOIN events e ON e.entity_type='trade' AND e.entity_id=t.id
JOIN proposals p ON p.id=t.proposal_id AND p.status='APPROVED'
WHERE e.status='SENT' AND e.level=0 AND e.message_id IS NOT NULL
 AND e.event_type IN ('ENTRY_CARD','ENTRY_CARD_PRIVATE')
 AND e.payload->>'publication_chat_id'='-1004417588627'
 AND t.status='TRACKING' AND t.private_close IS NULL
 AND NOT (t.deleted_channels ? '-1004417588627')
 AND coalesce(t.approval_quote->>'trade_mode','NORMAL')='NORMAL';
REVOKE ALL ON qa_execution_feed.signals,qa_execution_feed.control FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='qa_execution_reader') THEN
 GRANT USAGE ON SCHEMA qa_execution_feed TO qa_execution_reader;
 GRANT SELECT ON qa_execution_feed.signals,qa_execution_feed.control TO qa_execution_reader;
 END IF;
END $$;

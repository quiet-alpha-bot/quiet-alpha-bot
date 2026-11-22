CREATE TABLE IF NOT EXISTS execution_requests (
 id BIGSERIAL PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('LIST','CONFIRM','EXIT','CANCEL')),
 owner_id BIGINT NOT NULL, chat_id BIGINT NOT NULL, trade_id BIGINT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '2 minutes'
);
CREATE OR REPLACE VIEW qa_execution_feed.requests WITH (security_barrier=true) AS
 SELECT id,kind,chat_id,trade_id,created_at,expires_at FROM execution_requests
 WHERE kind IN ('LIST','EXIT') AND expires_at>now();
REVOKE ALL ON qa_execution_feed.requests FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='qa_execution_reader') THEN
 GRANT SELECT ON qa_execution_feed.requests TO qa_execution_reader;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION qa_execution_wakeup() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_notify('qa_execution_changed','');
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS qa_execution_event_wakeup ON events;
CREATE TRIGGER qa_execution_event_wakeup AFTER INSERT OR UPDATE ON events
 FOR EACH ROW WHEN (NEW.status='SENT' AND NEW.event_type IN ('ENTRY_CARD','ENTRY_CARD_PRIVATE')
 AND NEW.payload->>'publication_chat_id'='-1004417588627') EXECUTE FUNCTION qa_execution_wakeup();
DROP TRIGGER IF EXISTS qa_execution_request_wakeup ON execution_requests;
CREATE TRIGGER qa_execution_request_wakeup AFTER INSERT OR UPDATE ON execution_requests
 FOR EACH ROW EXECUTE FUNCTION qa_execution_wakeup();
DROP TRIGGER IF EXISTS qa_execution_control_wakeup ON execution_control;
CREATE TRIGGER qa_execution_control_wakeup AFTER UPDATE ON execution_control
 FOR EACH ROW EXECUTE FUNCTION qa_execution_wakeup();

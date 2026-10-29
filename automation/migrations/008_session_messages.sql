ALTER TABLE opening_prayers ADD COLUMN IF NOT EXISTS publication_choice TEXT NOT NULL DEFAULT 'both'
    CHECK (publication_choice IN ('public','private','both'));
CREATE TABLE IF NOT EXISTS closing_messages (
 id BIGSERIAL PRIMARY KEY,
 market_date DATE NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','SKIPPED','EXPIRED')),
 caption TEXT NOT NULL,
 public_chat_id TEXT NOT NULL,
 private_chat_id TEXT NOT NULL,
 publication_choice TEXT NOT NULL DEFAULT 'both' CHECK(publication_choice IN ('public','private','both')),
 expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 decided_at TIMESTAMPTZ,
 decided_by BIGINT
);

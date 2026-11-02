CREATE TABLE IF NOT EXISTS weekly_messages (
 id BIGSERIAL PRIMARY KEY,
 week_date DATE NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('WELCOME','INSTRUCTIONS')),
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','SKIPPED','SENT')),
 caption TEXT NOT NULL,
 public_chat_id TEXT NOT NULL,
 private_chat_id TEXT NOT NULL,
 publication_choice TEXT NOT NULL DEFAULT 'both' CHECK(publication_choice IN ('public','private','both')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 decided_at TIMESTAMPTZ,
 decided_by BIGINT,
 UNIQUE(week_date,kind)
);

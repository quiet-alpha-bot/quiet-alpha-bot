CREATE TABLE IF NOT EXISTS opening_prayers (
 id BIGSERIAL PRIMARY KEY,
 market_date DATE NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','SKIPPED','EXPIRED')),
 caption TEXT NOT NULL,
 asset_sha256 TEXT NOT NULL,
 public_chat_id TEXT NOT NULL,
 private_chat_id TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 decided_at TIMESTAMPTZ,
 decided_by BIGINT
);

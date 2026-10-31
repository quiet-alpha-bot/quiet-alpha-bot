CREATE TABLE IF NOT EXISTS session_finishes (
 market_date DATE PRIMARY KEY,
 publication_choice TEXT NOT NULL CHECK(publication_choice IN ('public','private','both')),
 requested_by BIGINT NOT NULL,
 requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

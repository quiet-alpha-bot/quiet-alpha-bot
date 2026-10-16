"""All SQL used by the automation layer (asyncpg $n placeholders).

Kept in one import-free module so every statement can be validated against
a real PostgreSQL schema (see tests/validate_sql.py).
"""

# Advisory lock keys (arbitrary constants, unique to this app).
LOCK_MIGRATIONS = 7120001
LOCK_LEADER = 7120002

# ---------------------------------------------------------
# Migrations
# ---------------------------------------------------------
MIGRATION_APPLIED = "SELECT 1 FROM schema_migrations WHERE version = $1"
MIGRATION_RECORD = (
    "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING"
)

# ---------------------------------------------------------
# Signals
# ---------------------------------------------------------
INSERT_SIGNAL = """
INSERT INTO signals (signal_id, source, direction, event_type, ticker,
                     timeframe, tv_price, signal_time, payload)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
ON CONFLICT (signal_id) DO NOTHING
RETURNING id
"""

CLAIM_SIGNAL = """
UPDATE signals
SET status = 'PROCESSING', processing_started_at = now(), updated_at = now()
WHERE id = $1 AND status = 'RECEIVED'
RETURNING id, signal_id, direction, event_type, ticker, timeframe,
          tv_price, signal_time, received_at, payload
"""

UNPROCESSED_SIGNALS = """
SELECT id FROM signals
WHERE status = 'RECEIVED' AND received_at < now() - make_interval(secs => $1)
ORDER BY id
"""

FAIL_STALE_PROCESSING = """
UPDATE signals
SET status = 'DATA_ERROR', status_reason = 'INTERRUPTED', processed_at = now(),
    updated_at = now()
WHERE status = 'PROCESSING'
  AND processing_started_at < now() - make_interval(secs => $1)
RETURNING id
"""

FINISH_SIGNAL = """
UPDATE signals
SET status = $2, status_reason = $3, processed_at = now(), updated_at = now()
WHERE id = $1
"""

SET_SIGNAL_CLASSIFICATION = """
UPDATE signals SET classification = $2, updated_at = now()
WHERE id = $1 AND classification IS NULL
"""

GET_SIGNAL = "SELECT * FROM signals WHERE id = $1"

SIGNALS_BEFORE_WATCH = """
SELECT id, direction, signal_time FROM signals
WHERE signal_time >= $1 AND signal_time < $2
ORDER BY signal_time
"""

# ---------------------------------------------------------
# UW watch
# ---------------------------------------------------------
INSERT_OBSERVATION = """
INSERT INTO uw_watch_observations (ticker, window_start, window_end, net_premium,
                                   strength, direction, triggered, metrics, error)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
RETURNING id
"""

LAST_GOOD_OBSERVATION = """
SELECT max(observed_at) FROM uw_watch_observations WHERE error IS NULL
"""

RECENT_WATCH_SAME_DIRECTION = """
SELECT id FROM uw_watches
WHERE direction = $1 AND watch_time > $2
ORDER BY watch_time DESC LIMIT 1
"""

INSERT_WATCH = """
INSERT INTO uw_watches (watch_key, direction, watch_time, expires_at, net_premium,
                        strength, context, observation_id, rules)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9::jsonb)
ON CONFLICT (watch_key) DO NOTHING
RETURNING id, direction, watch_time, expires_at, net_premium, strength
"""

WATCHES_IN_WINDOW = """
SELECT id, direction, watch_time, status, net_premium, strength FROM uw_watches
WHERE watch_time <= $1 AND watch_time >= $2
ORDER BY watch_time
"""

MARK_WATCHES = """
UPDATE uw_watches SET status = $2, status_changed_at = now()
WHERE id = ANY($1::bigint[]) AND status = 'INDICATOR_PENDING'
"""

EXPIRE_WATCHES = """
UPDATE uw_watches SET status = 'UW_ONLY', status_changed_at = now()
WHERE status = 'INDICATOR_PENDING' AND expires_at <= $1
RETURNING id, direction, watch_time, expires_at
"""

# ---------------------------------------------------------
# Classification records (never updated after insert)
# ---------------------------------------------------------
INSERT_MATCH = """
INSERT INTO signal_matches (classification, signal_db_id, watch_id,
                            indicator_direction, uw_direction,
                            indicator_signal_time, uw_watch_time,
                            time_difference_seconds, related_watch_ids,
                            watch_engine_active, rules_version, window_minutes,
                            classified_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::bigint[], $10, $11, $12, $13)
ON CONFLICT DO NOTHING
RETURNING id
"""

# ---------------------------------------------------------
# Selection / entry
# ---------------------------------------------------------
INSERT_RUN = """
INSERT INTO selection_runs (signal_db_id, settings) VALUES ($1, $2::jsonb)
RETURNING id
"""

FINISH_RUN = """
UPDATE selection_runs
SET finished_at = now(), status = $2, reason = $3, chain_ticker = $4,
    expiry = $5, spot = $6, candidates_total = $7, candidates_passed = $8,
    selected_symbol = $9, meta = $10::jsonb
WHERE id = $1
"""

INSERT_CANDIDATE = """
INSERT INTO contract_candidates (run_id, option_symbol, strike, expiry,
                                 option_type, bid, ask, last, mid, spread,
                                 spread_pct, volume, open_interest, delta,
                                 last_tape_time, passed, reject_reasons,
                                 score, score_breakdown, rank)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
        $16, $17::text[], $18, $19::jsonb, $20)
"""

INSERT_ENTRY_DECISION = """
INSERT INTO entry_decisions (run_id, decision, reason, bid, ask, spread_pct,
                             signal_age_seconds, inputs)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
"""

# ---------------------------------------------------------
# Proposals
# ---------------------------------------------------------
INSERT_PROPOSAL = """
INSERT INTO proposals (signal_db_id, run_id, direction, option_symbol, strike,
                       expiry, suggested_entry, bid, ask, last, classification,
                       uw_watch_id, details, expires_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14)
ON CONFLICT (signal_db_id) DO NOTHING
RETURNING *
"""

GET_PROPOSAL = "SELECT * FROM proposals WHERE id = $1"

SET_PROPOSAL_MESSAGE = """
UPDATE proposals SET message_id = $2, updated_at = now() WHERE id = $1
"""

SET_RECONFIRM_MESSAGE = """
UPDATE proposals SET reconfirm_message_id = $2, updated_at = now() WHERE id = $1
"""

BEGIN_APPROVAL = """
UPDATE proposals
SET status = 'APPROVING', approving_from = status, approving_since = now(),
    updated_at = now()
WHERE id = $1 AND status = $2
  AND (expires_at IS NULL OR expires_at > now())
RETURNING *
"""

REVERT_APPROVAL = """
UPDATE proposals
SET status = approving_from, approving_from = NULL, approving_since = NULL,
    updated_at = now()
WHERE id = $1 AND status = 'APPROVING'
"""

SET_NEEDS_RECONFIRM = """
UPDATE proposals
SET status = 'NEEDS_RECONFIRM', approving_from = NULL, approving_since = NULL,
    reconfirm_price = $2, reconfirm_quote = $3::jsonb, expires_at = $4,
    updated_at = now()
WHERE id = $1 AND status = 'APPROVING'
RETURNING *
"""

FINALIZE_APPROVAL = """
UPDATE proposals
SET status = 'APPROVED', approving_from = NULL, approving_since = NULL,
    decided_at = now(), decided_by = $2, decision_note = $3, updated_at = now()
WHERE id = $1 AND status = 'APPROVING'
RETURNING id
"""

DECIDE_PROPOSAL = """
UPDATE proposals
SET status = $3, decided_at = now(), decided_by = $4, decision_note = $5,
    updated_at = now()
WHERE id = $1 AND status = $2
RETURNING *
"""

EXPIRE_PROPOSALS = """
UPDATE proposals
SET status = 'EXPIRED', decided_at = now(), decision_note = 'expired',
    updated_at = now()
WHERE status IN ('PENDING_APPROVAL', 'NEEDS_RECONFIRM')
  AND expires_at IS NOT NULL AND expires_at <= now()
RETURNING *
"""

RECOVER_STUCK_APPROVING = """
UPDATE proposals
SET status = approving_from, approving_from = NULL, approving_since = NULL,
    updated_at = now()
WHERE status = 'APPROVING' AND approving_since < now() - make_interval(secs => $1)
RETURNING id
"""

# ---------------------------------------------------------
# Trades
# ---------------------------------------------------------
# Serialize approvals per direction so two fast button presses cannot exceed
# the three-open-trades limit.
LOCK_TRADE_DIRECTION = """
SELECT pg_advisory_xact_lock(hashtext('quiet-alpha-trades-' || $1))
"""

COUNT_TRACKING_DIRECTION = """
SELECT count(*) FROM trades
WHERE status = 'TRACKING' AND direction = $1
"""

INSERT_TRADE = """
INSERT INTO trades (proposal_id, signal_db_id, signal_id, option_symbol,
                    direction, strike, expiry, suggested_entry, approved_entry,
                    approval_quote, classification, uw_watch_id, signal_time,
                    approval_time, current_bid, current_ask, current_last)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13, $14,
        $15, $16, $17)
ON CONFLICT (proposal_id) DO NOTHING
RETURNING *
"""

GET_TRADE = "SELECT * FROM trades WHERE id = $1"

TRACKING_TRADES = "SELECT * FROM trades WHERE status = 'TRACKING' ORDER BY id"

RECENT_TRADES = "SELECT * FROM trades ORDER BY id DESC LIMIT $1"

DAILY_TRADES = """
SELECT t.* FROM trades AS t
JOIN proposals AS p ON p.id = t.proposal_id
WHERE (t.approval_time AT TIME ZONE 'America/New_York')::date = $1
  AND p.status = 'APPROVED'
  AND t.status IN ('TRACKING', 'CLOSED')
ORDER BY t.approval_time, t.id
"""

UPDATE_TRADE_QUOTE = """
UPDATE trades
SET current_bid = $2, current_ask = $3, current_last = $4,
    highest_bid = $5, highest_bid_time = $6, highest_mid = $7,
    highest_last = $8, lowest_bid = $9, lowest_bid_time = $10,
    last_poll_at = $11, last_tape_time = $12, last_error = NULL,
    updated_at = now()
WHERE id = $1 AND status = 'TRACKING'
"""

SET_TRADE_ERROR = """
UPDATE trades SET last_error = $2, last_poll_at = $3, updated_at = now()
WHERE id = $1
"""

RECORD_MILESTONE_DETECTIONS = """
UPDATE trades
SET highest_milestone_reached = GREATEST(highest_milestone_reached, $2),
    milestone_detections = $3::jsonb || milestone_detections,
    updated_at = now()
WHERE id = $1
"""

SET_TRADE_MILESTONE = """
UPDATE trades
SET highest_milestone_sent = GREATEST(highest_milestone_sent, $2), updated_at = now()
WHERE id = $1
"""

MARK_SUCCESS_DETECTED = """
UPDATE trades SET success_detected_at = $2, success_bid = $3, updated_at = now()
WHERE id = $1 AND success_detected_at IS NULL
RETURNING id
"""

MARK_SUCCESS_SENT = """
UPDATE trades SET success_sent = TRUE, updated_at = now() WHERE id = $1
"""

SET_TRACKING_MESSAGE = """
UPDATE trades SET tracking_message_id = $2, tracking_message_edited_at = now(),
    updated_at = now()
WHERE id = $1
"""

TOUCH_TRACKING_MESSAGE = """
UPDATE trades SET tracking_message_edited_at = now() WHERE id = $1
"""

CLOSE_TRADE = """
UPDATE trades SET status = 'CLOSED', close_reason = $2, closed_at = now(),
    updated_at = now()
WHERE id = $1 AND status = 'TRACKING'
RETURNING *
"""

INSERT_PRICE_OBSERVATION = """
INSERT INTO price_observations (trade_id, observed_at, bid, ask, last, volume,
                                open_interest, last_tape_time, latency_ms,
                                changed, error)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""

# ---------------------------------------------------------
# Event ledger
# ---------------------------------------------------------
CLAIM_EVENT = """
INSERT INTO events (entity_type, entity_id, event_type, level, status, attempts,
                    payload)
VALUES ($1, $2, $3, $4, 'PENDING', 1, $5::jsonb)
ON CONFLICT (entity_type, entity_id, event_type, level) DO NOTHING
RETURNING id
"""

RECLAIM_FAILED_EVENT = """
UPDATE events SET status = 'PENDING', attempts = attempts + 1, updated_at = now()
WHERE entity_type = $1 AND entity_id = $2 AND event_type = $3 AND level = $4
  AND status = 'FAILED' AND attempts < $5
RETURNING id
"""

MARK_EVENT = """
UPDATE events SET status = $2, message_id = $3, last_error = $4, updated_at = now()
WHERE id = $1
"""

RECORD_EVENT = """
INSERT INTO events (entity_type, entity_id, event_type, level, status, payload)
VALUES ($1, $2, $3, $4, 'RECORDED', $5::jsonb)
ON CONFLICT (entity_type, entity_id, event_type, level) DO NOTHING
RETURNING id
"""

# Stale PENDING (process died between claim and mark): never resent
# automatically, never treated as SENT -> owner reconciliation.
MARK_STALE_PENDING = """
UPDATE events
SET status = 'NEEDS_RECONCILIATION', last_error = 'STALE_PENDING', updated_at = now()
WHERE status = 'PENDING' AND updated_at < now() - make_interval(secs => $1)
RETURNING id, entity_type, entity_id, event_type, level
"""

# Owner decision: $2 = 'SENT' (message is in the channel) or 'FAILED' (resend).
RECONCILE_EVENT = """
UPDATE events
SET status = $2, last_error = 'RECONCILED_BY_OWNER', updated_at = now()
WHERE id = $1 AND status = 'NEEDS_RECONCILIATION'
RETURNING id, entity_type, entity_id, event_type, level, status
"""

EVENT_STATUS = """
SELECT status, message_id, attempts FROM events
WHERE entity_type = $1 AND entity_id = $2 AND event_type = $3 AND level = $4
"""

TRADE_EVENTS = """
SELECT event_type, level, status, created_at FROM events
WHERE entity_type = 'trade' AND entity_id = $1
ORDER BY created_at, level
"""

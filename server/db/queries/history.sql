-- history.sql   (Lesson 10 - GET /api/operations)
-- Plain reads: no transaction block needed.
-- Only committed operations exist in this table (rejected requests were rolled back),
-- so no status filter is needed.
-- ORDER: newest REQUEST first. operations.id is taken when the request reserves its key, before it
--   waits for row locks, so under concurrency a lower id can commit later. Ids are unique, not a
--   clock: do not present "balance after" values in this order. (ledger_entries.id does follow the
--   real per-account apply order, because ledger lines are inserted while the row lock is held.)
-- created_at: psycopg returns a timezone-aware datetime; FastAPI sends it as an ISO-8601 string
--   (e.g. "2026-10-16T09:30:00.123456Z"-style); the React client formats it for display.
-- APP DECIDES which statement to run, from the SESSION user:
--   PARTICIPANT -> history_for_account with account_id = the user's own account_id
--   AUDITOR     -> history_all

-- name: history_for_account
-- account_id = account_id of the logged-in participant (sent, received or converted).
-- The same named parameter appears twice; psycopg sends the value once.
SELECT o.id,
       o.operation_type,
       s.symbol,
       o.source_account_id,
       o.destination_account_id,
       o.quantity,
       u.username AS actor_username,
       o.created_at
FROM operations o
JOIN assets s     ON s.id = o.asset_id
LEFT JOIN users u ON u.id = o.actor_user_id   -- LEFT JOIN: OPENING rows have no actor (NULL)
WHERE o.source_account_id = %(account_id)s
   OR o.destination_account_id = %(account_id)s
ORDER BY o.id DESC
LIMIT 100;

-- name: history_all
-- AUDITOR: every operation.
SELECT o.id,
       o.operation_type,
       s.symbol,
       o.source_account_id,
       o.destination_account_id,
       o.quantity,
       u.username AS actor_username,
       o.created_at
FROM operations o
JOIN assets s     ON s.id = o.asset_id
LEFT JOIN users u ON u.id = o.actor_user_id
ORDER BY o.id DESC
LIMIT 100;

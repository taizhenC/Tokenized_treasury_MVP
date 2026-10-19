-- history.sql   (Lesson 10 - GET /api/operations; settlement operations added in Lesson 14)
-- Plain reads: no transaction block needed.
-- Only committed operations exist in this table (rejected requests were rolled back),
-- so no status filter is needed.
-- ORDER: newest REQUEST first. operations.id is taken when the request reserves its key, before it
--   waits for row locks, so under concurrency a lower id can commit later. Ids are unique, not a
--   clock: do not present "balance after" values in this order. (ledger_entries.id does follow the
--   real per-account apply order, because ledger lines are inserted while the row lock is held.)
-- created_at: psycopg returns a timezone-aware datetime; FastAPI sends it as an ISO-8601 string
--   (e.g. "2026-10-16T09:30:00.123456Z"-style); the React client formats it for display.
-- Lesson 14: operations also hold the cash leg (see migration 004):
--   * a SETTLEMENT_PROGRESS_PAYMENT, VALUED_CUTOFF or FINAL_SETTLEMENT moves no units, so its
--     asset_id and quantity are NULL. The join to assets is therefore a LEFT JOIN: a plain JOIN
--     would silently drop every such row from the history. symbol is then NULL.
--   * amount_cents (BIGINT, whole cents) is set for DELIVER_ORDER, DVP_SETTLEMENT and
--     SETTLEMENT_PROGRESS_PAYMENT, and NULL for every other type.
--   A participant never sees VALUED_CUTOFF or FINAL_SETTLEMENT: they name no account at all.
-- APP DECIDES which statement to run, from the SESSION user:
--   PARTICIPANT          -> history_for_account with account_id = the user's own account_id
--   AUDITOR or OPERATOR  -> history_all

-- name: history_for_account
-- account_id = account_id of the logged-in participant (sent, received, converted, delivered,
-- paid in, or settled as deliverer or receiver).
-- The same named parameter appears twice; psycopg sends the value once.
SELECT o.id,
       o.operation_type,
       s.symbol,
       o.source_account_id,
       o.destination_account_id,
       o.quantity,
       o.amount_cents,
       u.username AS actor_username,
       o.created_at
FROM operations o
LEFT JOIN assets s ON s.id = o.asset_id        -- LEFT JOIN: a payment or a day step has no asset (NULL)
LEFT JOIN users u  ON u.id = o.actor_user_id   -- LEFT JOIN: OPENING and DVP_SETTLEMENT rows have no actor
WHERE o.source_account_id = %(account_id)s
   OR o.destination_account_id = %(account_id)s
ORDER BY o.id DESC
LIMIT 100;
-- APP DECIDES: sends the rows as they are (amount_cents and symbol may be NULL, sent as null).

-- name: history_all
-- AUDITOR or OPERATOR: every operation.
SELECT o.id,
       o.operation_type,
       s.symbol,
       o.source_account_id,
       o.destination_account_id,
       o.quantity,
       o.amount_cents,
       u.username AS actor_username,
       o.created_at
FROM operations o
LEFT JOIN assets s ON s.id = o.asset_id
LEFT JOIN users u  ON u.id = o.actor_user_id
ORDER BY o.id DESC
LIMIT 100;
-- APP DECIDES: sends the rows as they are, newest id first.

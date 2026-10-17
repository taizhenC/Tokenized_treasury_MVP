-- transfer.sql   (Lesson 7; idempotency parts added in Lesson 8)
-- POST /api/transfers  { "toAccountId": 102, "quantity": 25, "idempotencyKey": "..." }
-- Moves q TOKENIZED units from the user's own account to a DIFFERENT, approved account.
-- Ownership changes; the total does not.
--
-- BEFORE any SQL the APP has already:
--   * loaded the session user; role must be PARTICIPANT (AUDITOR -> 403)
--   * set source_account_id = the user's own account_id (from the SESSION, never from the body;
--     a body that names any account other than toAccountId is rejected with 400)
--   * validated the body with Pydantic (else 400): toAccountId a whole number 1..2147483647
--     (INTEGER range); quantity a whole number 1..1,000,000; idempotencyKey 1..100 characters
--   * rejected toAccountId == source with 400 (the CHECK operations_no_self_transfer is the backstop)
--   * computed request_hash = sha256 hex of a canonical string, always in this order:
--       type|source|destination|asset_id|quantity   e.g. 'TRANSFER|101|102|1|25'
--
-- PARAMETERS ARE NAMED. One dict per request, passed to every statement:
--   {actor_user_id, source_account_id, destination_account_id, asset_id, quantity,
--    idempotency_key, request_hash} + operation_id (after reserve) + result (at the end).
--
-- ONE CONNECTION, ONE TRANSACTION:  with conn.transaction():  around every statement below.
--   psycopg sends BEGIN / COMMIT / ROLLBACK, so this file has no begin/commit/rollback blocks.
-- ANY rejection, replay or conflict raises inside the block -> ROLLBACK. No operations row survives.
-- RULE: after EVERY UPDATE check cursor.rowcount == 1, otherwise raise (-> ROLLBACK, 500).
-- ERROR MAPPING (after ROLLBACK): ForeignKeyViolation 23503 (unknown account, raised by reserve)
--   -> 422; CheckViolation 23514 (a CHECK backstop) -> 422; OperationalError (connection lost,
--   pool timeout, deadlock) -> 503; anything else -> 500.

-- name: reserve_idempotency_key
INSERT INTO operations
  (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id,
   quantity, idempotency_key, request_hash)
VALUES
  ('TRANSFER', %(actor_user_id)s, %(source_account_id)s, %(destination_account_id)s, %(asset_id)s,
   %(quantity)s, %(idempotency_key)s, %(request_hash)s)
ON CONFLICT (actor_user_id, idempotency_key) DO NOTHING
RETURNING id;
-- APP DECIDES:
--   1 row  -> new request: operation_id = row["id"], continue with lock_positions.
--   0 rows -> this actor already used this key: run find_existing_operation.
--   ForeignKeyViolation -> the destination account does not exist: ROLLBACK, 422.
--   (A concurrent request with the same key WAITS here until the first one finishes:
--    it commits -> 0 rows (replay); it rolls back -> 1 row (this request runs normally).)

-- name: find_existing_operation
SELECT id, request_hash, result
FROM operations
WHERE actor_user_id = %(actor_user_id)s
  AND idempotency_key = %(idempotency_key)s;
-- APP DECIDES (then ROLLBACK - nothing was written):
--   same request_hash      -> replay: HTTP 200 + the stored result. The effect is NOT applied again.
--   different request_hash -> HTTP 409 Conflict.

-- name: lock_positions
-- Lock BOTH rows in ONE statement, always lowest account_id first, whichever is the source.
-- ORDER BY is applied below the row-locking step, so rows are locked in account_id order whether
-- the planner uses the primary-key index or a Sort. So A->B and B->A both lock A first and queue
-- up instead of deadlocking. A second transfer from the same account WAITS here until the first
-- one commits, and then reads the NEW balance.
SELECT account_id, tokenized_units
FROM positions
WHERE account_id IN (%(source_account_id)s, %(destination_account_id)s)
  AND asset_id = %(asset_id)s
ORDER BY account_id
FOR UPDATE;
-- APP DECIDES:
--   not exactly 2 rows -> 500 (every account's position row is pre-created by the seed).
--   Rows come back in account_id order, NOT source first: the app builds
--   {row["account_id"]: row for row in rows} and reads the source row by its id. Never rows[0].
--   source row's tokenized_units < quantity -> 422 "insufficient tokenized units".

-- name: read_approvals
-- Plain read (no lock needed: no MVP endpoint changes is_approved).
SELECT id, is_approved
FROM accounts
WHERE id IN (%(source_account_id)s, %(destination_account_id)s);
-- APP DECIDES: not exactly 2 rows, or any row with is_approved false -> 422 "wallet is not approved".

-- name: debit_source
UPDATE positions
SET tokenized_units = tokenized_units - %(quantity)s
WHERE account_id = %(source_account_id)s
  AND asset_id = %(asset_id)s
RETURNING tokenized_units;
-- APP DECIDES: rowcount must be 1, otherwise raise.
-- Safety net: the CHECK (tokenized_units >= 0) rejects a negative balance even if the app check is wrong.

-- name: credit_destination
UPDATE positions
SET tokenized_units = tokenized_units + %(quantity)s
WHERE account_id = %(destination_account_id)s
  AND asset_id = %(asset_id)s
RETURNING tokenized_units;
-- APP DECIDES: rowcount must be 1, otherwise raise.
--   The new balance (RETURNING) is useful when you try the block yourself, but the app does NOT
--   send it to the client: it is the recipient's balance, and a participant sees only their own.

-- name: insert_ledger_entries
-- Two TOKENIZED lines from the SAME quantity parameter: -q for the source, +q for the destination.
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES
  (%(operation_id)s, %(source_account_id)s,      %(asset_id)s, 'TOKENIZED', -%(quantity)s),
  (%(operation_id)s, %(destination_account_id)s, %(asset_id)s, 'TOKENIZED',  %(quantity)s);
-- APP DECIDES: rowcount must be 2.

-- name: store_result
-- result = Jsonb(result_dict) - what the API returns, and what a replay returns
UPDATE operations
SET result = %(result)s
WHERE id = %(operation_id)s;
-- APP DECIDES: rowcount must be 1, otherwise raise. Then the block ends -> COMMIT -> 201.

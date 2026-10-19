-- convert_to_tokenized.sql   (Lesson 6; idempotency parts added in Lesson 8; collateral monitor in Lesson 12)
-- POST /api/conversions  { "direction": "TO_TOKENIZED", "quantity": 100, "idempotencyKey": "..." }
-- Mint, inside ONE account: traditional_units -= q, tokenized_units += q. The total does not change.
--
-- Lesson 12: a mint also takes COLLATERAL away. Tokenized units have no collateral value (under the
--   SEC staff no-action letter of 11 Dec 2025, DTC gives tokenized entitlements no collateral value),
--   so the q units leave settlement_accounts.collateral_value_cents at q x collateral_cents_per_unit.
--   An account in debit could then fall below zero on its Collateral Monitor (collateral + net), so
--   the app checks the CM BEFORE the mint and refuses a breaching mint at once with a 422 (a lab
--   choice: a mint is not a delivery, so it does not recycle). The net debit cap cannot be affected:
--   a mint moves no money, so net_cents does not change.
--
-- BEFORE any SQL the APP has already:
--   * loaded the session user; role must be PARTICIPANT (AUDITOR -> 403)
--   * taken actor_user_id and account_id from the SESSION user row (never from the request body)
--   * validated the body with Pydantic (else 400): quantity a whole number 1..1,000,000;
--     idempotencyKey a string of 1..100 characters; no other fields allowed
--   * computed request_hash = sha256 hex of a canonical string, always in this order:
--       type|account_id|asset_id|quantity   e.g. 'CONVERT_TO_TOKENIZED|101|1|100'
--
-- PARAMETERS ARE NAMED. The app builds ONE dict per request and passes it to every statement:
--   {actor_user_id, account_id, asset_id, quantity, idempotency_key, request_hash}
--   + operation_id (after reserve) + result (just before the end).
--   Each statement picks the names it needs; extra names are ignored, a missing name is an error.
-- LOCK ORDER (the one global order, Lesson 12): the position row first, then the settlement account.
--   The settlement engine locks all positions, then all settlement accounts, so a mint and a deliver
--   order take their rows in the same order and can never wait for each other in a circle (deadlock).
--
-- ONE CONNECTION, ONE TRANSACTION: the app takes one connection from the pool and runs every
--   statement below inside  with conn.transaction():  - psycopg sends BEGIN before the first
--   statement and COMMIT at the end of the block, or ROLLBACK if anything raises. So this file has
--   NO begin/commit/rollback blocks.
-- ANY rejection (422), replay (200) or conflict (409) raises inside the block -> ROLLBACK.
--   Nothing is saved, the reserved key is released, and the app logs the rejection itself.
-- RULE: after EVERY UPDATE check cursor.rowcount == 1, otherwise raise (-> ROLLBACK, 500).
--   An UPDATE that matches no row is NOT an SQL error - it just changes nothing.
-- ERROR MAPPING (after ROLLBACK): CheckViolation 23514 (a CHECK backstop) -> 422;
--   OperationalError (connection lost 57P01/08xxx, pool timeout) -> 503; anything else -> 500.

-- name: reserve_idempotency_key
-- Claim the key FIRST. If another request with the same actor + key is still running,
-- this INSERT waits on the unique index until that one commits or rolls back.
INSERT INTO operations
  (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES
  ('CONVERT_TO_TOKENIZED', %(actor_user_id)s, %(account_id)s, %(asset_id)s, %(quantity)s,
   %(idempotency_key)s, %(request_hash)s)
ON CONFLICT (actor_user_id, idempotency_key) DO NOTHING
RETURNING id;
-- APP DECIDES:
--   1 row  -> new request: operation_id = row["id"], continue with lock_position.
--   0 rows (fetchone() is None) -> this actor already used this key: run find_existing_operation.

-- name: find_existing_operation
SELECT id, request_hash, result
FROM operations
WHERE actor_user_id = %(actor_user_id)s
  AND idempotency_key = %(idempotency_key)s;
-- APP DECIDES (then ROLLBACK - nothing was written in this transaction):
--   same request_hash      -> replay: HTTP 200 + the stored result (psycopg returns JSONB as a
--                             Python dict). The effect is NOT applied again.
--   different request_hash -> HTTP 409 Conflict: the key was reused for a different request.

-- name: lock_position
-- Lock this account's position row until COMMIT/ROLLBACK, then read the CURRENT balance.
SELECT traditional_units, tokenized_units
FROM positions
WHERE account_id = %(account_id)s
  AND asset_id = %(asset_id)s
FOR UPDATE;
-- APP DECIDES: 0 rows -> 500 (seeded positions always exist).
--              traditional_units < quantity -> 422 "insufficient traditional units".

-- name: lock_settlement_account
-- Lesson 12. Lock this account's settlement row too - AFTER its position row (see LOCK ORDER) -
-- then read its CURRENT net and collateral: no deliver order can change them until this
-- transaction ends.
SELECT net_cents, collateral_value_cents, net_debit_cap_cents
FROM settlement_accounts
WHERE account_id = %(account_id)s
FOR UPDATE;
-- APP DECIDES: 0 rows -> 500 (seed 004 creates one settlement account per account).

-- name: read_collateral_price
-- Lesson 12. What one unit is worth as collateral: prior close less the haircut, computed by
-- Postgres (a generated column). No lock: no endpoint changes prices.
SELECT prior_close_cents, haircut_bps, collateral_cents_per_unit
FROM collateral_prices
WHERE asset_id = %(asset_id)s;
-- APP DECIDES: 0 rows -> 500 (seed 004 prices the instrument).
--   Then risk.mint_problem(...): the CM after the mint would be
--     (collateral_value_cents - quantity x collateral_cents_per_unit) + net_cents
--   below 0 -> 422 "collateral monitor would go negative: tokenized units have no collateral value".
--   Demo: gamma, 1,100 traditional units, net -1,000,000 (it received 100 units for $10,000):
--     mint 995 -> 105 x 9,500 - 1,000,000 = -2,500 -> 422;  mint 994 -> 106 x 9,500 - 1,000,000 = 7,000 -> 201.

-- name: apply_conversion
UPDATE positions
SET traditional_units = traditional_units - %(quantity)s,
    tokenized_units   = tokenized_units   + %(quantity)s
WHERE account_id = %(account_id)s
  AND asset_id = %(asset_id)s
RETURNING traditional_units, tokenized_units;
-- APP DECIDES: rowcount must be 1, otherwise raise. RETURNING gives the new balances for the result.
-- Safety net: the CHECK (traditional_units >= 0) rejects this if the app check above has a bug.

-- name: update_collateral
-- Lesson 12. The q units that just became tokenized stop counting as collateral.
-- A relative update (col = col - delta), so the CHECKs judge the real new value.
-- The price comes from a subquery on collateral_prices, not from a parameter: the database reads
-- the same number the app checked (no endpoint changes prices), and nobody can send a different one.
-- quantity (INTEGER) x collateral_cents_per_unit (BIGINT) is a BIGINT: exact whole cents.
UPDATE settlement_accounts
SET collateral_value_cents = collateral_value_cents
      - %(quantity)s * (SELECT collateral_cents_per_unit FROM collateral_prices WHERE asset_id = %(asset_id)s)
WHERE account_id = %(account_id)s
RETURNING collateral_value_cents;
-- APP DECIDES: rowcount must be 1, otherwise raise. The result body does not show it (unchanged).
-- Safety net: CHECK settlement_collateral_monitor_nonnegative (collateral + net >= 0) rejects a
--   breach the app check missed -> 422 "collateral monitor would go negative".

-- name: insert_ledger_entries
-- One debit line and one credit line from the SAME parameter, so they always net to zero.
-- No cast is needed on the minus sign: psycopg sends a Python int as a typed integer parameter,
-- so Postgres knows what "-" applies to. (A value sent as text would fail with
-- "operator is not unique" - a loud error, which is what we want for a type slip.)
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES
  (%(operation_id)s, %(account_id)s, %(asset_id)s, 'TRADITIONAL', -%(quantity)s),
  (%(operation_id)s, %(account_id)s, %(asset_id)s, 'TOKENIZED',    %(quantity)s);
-- APP DECIDES: rowcount must be 2.

-- name: store_result
-- The response body, stored so a replay of the same key returns it without re-applying.
-- result = Jsonb(result_dict)  (psycopg.types.json.Jsonb; a bare dict raises "cannot adapt type 'dict'")
UPDATE operations
SET result = %(result)s
WHERE id = %(operation_id)s;
-- APP DECIDES: rowcount must be 1, otherwise raise. Then the transaction block ends -> COMMIT -> 201.

-- settlement_payment.sql   (Lesson 12 - POST /api/settlement-progress-payments)
-- POST /api/settlement-progress-payments  { "amountCents": 50000, "idempotencyKey": "..." }
-- A settlement progress payment: the participant wires Fed funds to DTC during the day. It credits
-- the participant's settlement balance (net_cents goes up), so its debit shrinks and its Collateral
-- Monitor grows - and a delivery that was waiting for exactly that room can now settle.
-- Simulation only: no money moves anywhere; the Fed wire is one DTC_FED_ACCOUNT line in a table.
--
-- BEFORE any SQL the APP has already:
--   * loaded the session user; role must be PARTICIPANT (AUDITOR, OPERATOR -> 403)
--   * set account_id = the user's own account_id (from the SESSION, never from the body)
--   * validated the body with Pydantic (else 400): amountCents whole cents 1..10,000,000,000,000;
--     idempotencyKey 1..100 characters; no other fields allowed
--   * computed request_hash = sha256 hex of a canonical string, always in this order:
--       type|account_id|amount_cents   e.g. 'SETTLEMENT_PROGRESS_PAYMENT|103|50000'
--
-- PARAMETERS ARE NAMED. One dict per request, passed to every statement:
--   {actor_user_id, account_id, amount_cents, idempotency_key, request_hash}
--   + operation_id (after reserve) + business_date (from the engine's lock) + result (at the end).
--
-- ONE CONNECTION, ONE TRANSACTION:  with conn.transaction():  around every statement of the request:
--   1 reserve_idempotency_key                                    (this file)
--   2 the engine locks the sequencer, all positions, all settlement accounts,
--     then reads the price                                       (settlement_engine.sql)
--   3 phase must be OPEN, else 422 "valued processing is closed: the valued cutoff has passed"
--     (a lab choice: no payments after the valued cutoff)
--   4 credit_settlement_payment, insert_payment_cash_entries     (this file)
--   5 the engine retries every waiting order: this credit may be what one of them waits for
--   6 store_result -> COMMIT -> 201
-- ANY rejection, replay or conflict raises inside the block -> ROLLBACK. No operations row survives.
-- RULE: after EVERY UPDATE check cursor.rowcount == 1, otherwise raise (-> ROLLBACK, 500).
-- ERROR MAPPING (after ROLLBACK): CheckViolation 23514 (a CHECK backstop) -> 422; OperationalError
--   (connection lost, pool timeout, lock_timeout) -> 503; anything else -> 500.

-- name: reserve_idempotency_key
-- Claim the key FIRST, before any lock. The payment is this SETTLEMENT_PROGRESS_PAYMENT row:
-- the payer is the destination (its balance is credited); source_account_id, asset_id and quantity
-- are not named, so they stay NULL - a payment moves no units (operations_columns_match_type
-- requires exactly this shape).
INSERT INTO operations
  (operation_type, actor_user_id, destination_account_id, idempotency_key, request_hash, amount_cents)
VALUES
  ('SETTLEMENT_PROGRESS_PAYMENT', %(actor_user_id)s, %(account_id)s, %(idempotency_key)s,
   %(request_hash)s, %(amount_cents)s)
ON CONFLICT (actor_user_id, idempotency_key) DO NOTHING
RETURNING id;
-- APP DECIDES:
--   1 row  -> new request: operation_id = row["id"], continue with the engine's locks.
--   0 rows -> this actor already used this key: run find_existing_operation.

-- name: find_existing_operation
SELECT id, request_hash, result
FROM operations
WHERE actor_user_id = %(actor_user_id)s
  AND idempotency_key = %(idempotency_key)s;
-- APP DECIDES (then ROLLBACK - nothing was written):
--   same request_hash      -> replay: HTTP 200 + the stored result. The money is NOT credited again.
--   different request_hash -> HTTP 409 Conflict.

-- name: credit_settlement_payment
-- The credit, as a RELATIVE update (net_cents = net_cents + amount) on the row the engine locked.
-- Collateral does not change: a payment moves money, not units.
-- In this lab a payment larger than the debit leaves a credit (net_cents > 0), paid back at the end
-- of the day like any other credit.
UPDATE settlement_accounts
SET net_cents = net_cents + %(amount_cents)s
WHERE account_id = %(account_id)s
RETURNING account_id, net_cents, collateral_value_cents, net_debit_cap_cents;
-- APP DECIDES: rowcount must be 1, otherwise raise. RETURNING refreshes the engine's copy of the
--   account before it retries the waiting orders.

-- name: insert_payment_cash_entries
-- Two cash lines from the SAME amount, so they net to zero:
--   PARTICIPANT_SETTLEMENT  +amount  the payer's settlement balance is credited
--   DTC_FED_ACCOUNT         -amount  the Fed funds came in through DTC's account at the Fed
--                                    (a Fed line names no account: the CHECK cash_line_shape)
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES
  (%(operation_id)s, %(business_date)s, 'PARTICIPANT_SETTLEMENT', %(account_id)s,  %(amount_cents)s),
  (%(operation_id)s, %(business_date)s, 'DTC_FED_ACCOUNT',        NULL,           -%(amount_cents)s);
-- APP DECIDES: rowcount must be 2. Then the engine retries the waiting orders.

-- name: store_result
-- result = Jsonb(result_dict) - what the API returns, and what a replay returns
UPDATE operations
SET result = %(result)s
WHERE id = %(operation_id)s;
-- APP DECIDES: rowcount must be 1, otherwise raise. Then the block ends -> COMMIT -> 201.

-- deliver_order.sql   (Lesson 12 - POST /api/delivery-orders)
-- POST /api/delivery-orders
--   { "toAccountId": 102, "quantity": 200, "amountCents": 2000000, "idempotencyKey": "..." }
-- A valued delivery (DVP, delivery versus payment): q TRADITIONAL units from the user's own account
-- to another account, against amountCents. It either SETTLES at once (units and money move: 201)
-- or RECYCLES (it would break a risk control: it waits and is retried after every credit; it settles
-- if a credit makes room before the valued cutoff, otherwise it is DROPPED_AT_CUTOFF: 202).
-- Simulation only: modelled on public descriptions of DTC's settlement service, not affiliated with DTCC.
--
-- BEFORE any SQL the APP has already:
--   * loaded the session user; role must be PARTICIPANT (AUDITOR, OPERATOR -> 403)
--   * set deliverer_account_id = the user's own account_id (from the SESSION, never from the body)
--   * validated the body with Pydantic (else 400): toAccountId a whole number 1..2147483647;
--     quantity a whole number 1..1,000,000; amountCents whole cents 1..10,000,000,000,000
--     ($0.01 .. $100 billion); idempotencyKey 1..100 characters; no other fields allowed
--   * rejected toAccountId == own account with 400 "cannot deliver to yourself"
--     (the CHECKs operations_no_self_transfer and delivery_no_self_delivery are the backstops)
--   * computed request_hash = sha256 hex of a canonical string, always in this order:
--       type|deliverer|receiver|asset_id|quantity|amount_cents   e.g. 'DELIVER_ORDER|101|102|1|200|2000000'
--
-- PARAMETERS ARE NAMED. One dict per request, passed to every statement:
--   {actor_user_id, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents,
--    idempotency_key, request_hash} + operation_id (after reserve)
--   + submitted_operation_id, business_date, status, recycle_reason, settlement_operation_id
--     (for insert_delivery_instruction) + result (at the end).
--
-- ONE CONNECTION, ONE TRANSACTION:  with conn.transaction():  around every statement of the request:
--   1 reserve_idempotency_key                                    (this file)
--   2 the engine locks the sequencer, all positions, all settlement accounts,
--     then reads the price                                       (settlement_engine.sql)
--   3 phase must be OPEN, else 422 "valued processing is closed: the valued cutoff has passed"
--   4 the app checks the risk controls on the LOCKED rows        (app/domain/risk.py)
--       pass -> the engine settles it, insert_delivery_instruction (SETTLED), then the engine
--               retries the waiting orders: the new credit may be what one of them waits for
--       fail -> insert_delivery_instruction (RECYCLING, with the reason); no retry: nothing changed
--   5 store_result -> COMMIT -> 201 (SETTLED) or 202 (RECYCLING)
-- ANY rejection, replay or conflict raises inside the block -> ROLLBACK. No operations row survives.
-- RULE: after EVERY UPDATE check cursor.rowcount == 1, otherwise raise (-> ROLLBACK, 500).
-- ERROR MAPPING (after ROLLBACK): ForeignKeyViolation 23503 (unknown receiver, raised by reserve)
--   -> 422 "unknown account"; CheckViolation 23514 (a CHECK backstop) -> 422; OperationalError
--   (connection lost, pool timeout, lock_timeout) -> 503; anything else -> 500.

-- name: reserve_idempotency_key
-- Claim the key FIRST, before any lock. The order itself is this DELIVER_ORDER row: who asked,
-- deliverer (source), receiver (destination), what, how many, and for how much.
INSERT INTO operations
  (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id,
   quantity, idempotency_key, request_hash, amount_cents)
VALUES
  ('DELIVER_ORDER', %(actor_user_id)s, %(deliverer_account_id)s, %(receiver_account_id)s, %(asset_id)s,
   %(quantity)s, %(idempotency_key)s, %(request_hash)s, %(amount_cents)s)
ON CONFLICT (actor_user_id, idempotency_key) DO NOTHING
RETURNING id;
-- APP DECIDES:
--   1 row  -> new request: operation_id = row["id"], continue with the engine's locks.
--   0 rows -> this actor already used this key: run find_existing_operation.
--   ForeignKeyViolation -> the receiver account does not exist: ROLLBACK, 422 "unknown account".
--   (A concurrent request with the same key WAITS here until the first one finishes:
--    it commits -> 0 rows (replay); it rolls back -> 1 row (this request runs normally).)

-- name: find_existing_operation
SELECT id, request_hash, result
FROM operations
WHERE actor_user_id = %(actor_user_id)s
  AND idempotency_key = %(idempotency_key)s;
-- APP DECIDES (then ROLLBACK - nothing was written):
--   same request_hash      -> replay: HTTP 200 + the stored result. The order is NOT placed again.
--   different request_hash -> HTTP 409 Conflict.

-- name: insert_delivery_instruction
-- The INDEPENDENT RECORD OF THE INSTRUCTION: what was asked, kept apart from the ledger lines, so
-- reconciliation can later compare every settled order with what was actually moved.
--   submitted_operation_id   = operation_id, this request's DELIVER_ORDER row
--   business_date            = the day the engine locked (settlement_day.business_date)
--   status SETTLED           -> settlement_operation_id = its DVP_SETTLEMENT row, recycle_reason NULL
--   status RECYCLING         -> settlement_operation_id NULL, recycle_reason = why it waits
-- The CHECKs delivery_settled_has_operation and delivery_reason_matches_status reject any other mix.
-- recycle_reason is stored EXACTLY (e.g. RECEIVER_NET_DEBIT_CAP); masking it for the other side is
-- done by the app when it answers, never in the stored row.
INSERT INTO delivery_instructions
  (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id,
   quantity, amount_cents, status, recycle_reason, settlement_operation_id)
VALUES
  (%(submitted_operation_id)s, %(business_date)s, %(deliverer_account_id)s, %(receiver_account_id)s, %(asset_id)s,
   %(quantity)s, %(amount_cents)s, %(status)s, %(recycle_reason)s, %(settlement_operation_id)s)
RETURNING id;
-- APP DECIDES: instruction_id = row["id"] (the "instructionId" of the result).
--   SETTLED -> the engine then retries the waiting orders (list_recycling_instructions).

-- name: store_result
-- result = Jsonb(result_dict) - what the API returns, and what a replay returns
UPDATE operations
SET result = %(result)s
WHERE id = %(operation_id)s;
-- APP DECIDES: rowcount must be 1, otherwise raise. Then the block ends -> COMMIT
--   -> 201 if the order SETTLED, 202 (Accepted: it waits) if it is RECYCLING.

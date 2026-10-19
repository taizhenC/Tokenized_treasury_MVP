-- valued_cutoff.sql   (Lesson 13 - the settlement day, step 1 of 2)
-- POST /api/settlement-day/valued-cutoff  { "idempotencyKey": "..." }   (operator only)
-- Closes VALUED processing for today: every deliver order still RECYCLING is DROPPED, and the
-- day's phase moves from OPEN to VALUED_CUTOFF_PASSED. Moves no units and no money.
--
-- What DTC does at this step: during the day, a valued delivery that lacks position or would
-- break the Collateral Monitor or the Net Debit Cap recycles (pends) and is retried later (in this
-- lab: on every credit event - a delivery that settles, or a settlement progress payment).
-- At about 3:10pm ET, valued items still pending DROP: they will not settle that day.
-- The lab simulates the clock with a button: the operator runs this step; nothing runs by time.
-- Lab choices: a dropped order never settles (a new order is needed on another day), and after
--   this step the app refuses new deliver orders and settlement progress payments (422) until
--   final settlement opens the next business day.
--
-- BEFORE any SQL the APP has already:
--   * loaded the session user; role must be OPERATOR (PARTICIPANT or AUDITOR -> 403 "operators only")
--   * validated the body with Pydantic (else 400): idempotencyKey 1..100 characters, no other
--     fields. The request has NO other input: which day is cut off comes from the database.
--   * computed request_hash = sha256 hex of a canonical string with only the type:
--       'VALUED_CUTOFF'   (there is nothing else to hash, so one key always means one request)
--
-- PARAMETERS ARE NAMED. One dict per request, passed to every statement that needs one:
--   {actor_user_id, idempotency_key, request_hash} + operation_id (after reserve)
--   + business_date (read from lock_settlement_day) + result (at the end).
--
-- ONE CONNECTION, ONE TRANSACTION:  with conn.transaction():  around every statement below.
--   psycopg sends BEGIN / COMMIT / ROLLBACK, so this file has no begin/commit/rollback blocks.
-- ANY rejection, replay or conflict raises inside the block -> ROLLBACK. No operations row survives.
-- LOCK ORDER (the one global order: sequencer -> positions -> settlement accounts): this step
--   takes the SEQUENCER ONLY. It changes no position and no settlement balance.
-- RULE: after the UPDATEs that must change one row, check cursor.rowcount == 1, otherwise raise
--   (-> ROLLBACK, 500).
-- ERROR MAPPING (after ROLLBACK): OperationalError (connection lost, pool timeout, lock_timeout)
--   -> 503; anything else -> 500.

-- name: reserve_idempotency_key
-- Claim the key FIRST, exactly like every other write. A VALUED_CUTOFF row names no account,
-- no asset, no quantity and no amount: the CHECK operations_columns_match_type requires all of
-- them to be NULL for this type, so the INSERT simply does not list those columns.
INSERT INTO operations
  (operation_type, actor_user_id, idempotency_key, request_hash)
VALUES
  ('VALUED_CUTOFF', %(actor_user_id)s, %(idempotency_key)s, %(request_hash)s)
ON CONFLICT (actor_user_id, idempotency_key) DO NOTHING
RETURNING id;
-- APP DECIDES:
--   1 row  -> new request: operation_id = row["id"], continue with lock_settlement_day.
--   0 rows -> this operator already used this key: run find_existing_operation.

-- name: find_existing_operation
SELECT id, request_hash, result
FROM operations
WHERE actor_user_id = %(actor_user_id)s
  AND idempotency_key = %(idempotency_key)s;
-- APP DECIDES (then ROLLBACK - nothing was written):
--   same request_hash      -> replay: HTTP 200 + the stored result. The cutoff does NOT run again.
--   different request_hash -> HTTP 409 Conflict.

-- name: lock_settlement_day
-- The SETTLEMENT SEQUENCER: the one settlement_day row. Every request that can settle, recycle or
-- drop a delivery (deliver order, settlement progress payment, valued cutoff, final settlement)
-- locks it FIRST, so they run one at a time. A deliver order that is settling right now finishes
-- before the cutoff starts; one that arrives during the cutoff waits, then sees the new phase.
-- No WHERE clause: the table has exactly one row (singleton PRIMARY KEY, CHECK (singleton)).
-- READ COMMITTED + FOR UPDATE: a request that had to WAIT for this lock re-reads the row's NEWEST
-- committed version once the lock is free - so a second cutoff sees phase VALUED_CUTOFF_PASSED.
SELECT business_date, phase, max_net_debit_cap_cents
FROM settlement_day
FOR UPDATE;
-- APP DECIDES:
--   0 rows -> 500 (the seed always creates the row).
--   phase <> 'OPEN' -> 422 "the valued cutoff has already run today".
--   Otherwise business_date = row["business_date"] (the day being cut off), continue.

-- name: drop_recycling_instructions
-- Every order of TODAY that is still waiting becomes DROPPED_AT_CUTOFF. Only today's: the WHERE
-- names the business date read under the lock above.
-- recycle_reason is NOT cleared: a dropped order keeps saying WHY it never settled (the CHECK
-- delivery_reason_matches_status requires a reason for every status except SETTLED).
-- Rows that are already SETTLED or DROPPED are not touched.
UPDATE delivery_instructions
SET status = 'DROPPED_AT_CUTOFF'
WHERE status = 'RECYCLING'
  AND business_date = %(business_date)s
RETURNING id;
-- APP DECIDES: any number of rows is correct (0 when nothing was waiting).
--   RETURNING has no ORDER BY, so the app sorts the ids before putting them in the result.

-- name: close_valued_processing
-- The day row is UPDATED IN PLACE (never deleted and re-inserted): a deliver order waiting on the
-- sequencer lock then re-reads this same row and finds phase VALUED_CUTOFF_PASSED (-> 422). Had the
-- row been deleted, the waiting FOR UPDATE would find no row at all.
-- WHERE phase = 'OPEN' repeats the app's check in SQL: if the phase was somehow not OPEN, this
-- changes 0 rows and the rowcount rule stops the request.
UPDATE settlement_day
SET phase = 'VALUED_CUTOFF_PASSED'
WHERE phase = 'OPEN'
RETURNING business_date, phase;
-- APP DECIDES: rowcount must be 1, otherwise raise.

-- name: store_result
-- result = Jsonb(result_dict) - what the API returns, and what a replay returns
--   e.g. {"operationId": 60, "type": "VALUED_CUTOFF", "businessDate": "2026-10-19", "droppedInstructionIds": [10]}
UPDATE operations
SET result = %(result)s
WHERE id = %(operation_id)s;
-- APP DECIDES: rowcount must be 1, otherwise raise. Then the block ends -> COMMIT -> 201.

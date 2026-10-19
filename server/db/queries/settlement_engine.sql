-- settlement_engine.sql   (Lesson 12 - the settlement engine: settle, recycle and retry deliveries)
-- No endpoint of its own. Every request that can settle a delivery runs these statements inside
-- ITS OWN transaction, after reserving its own idempotency key:
--   POST /api/delivery-orders               (deliver_order.sql)       settles or recycles ONE new order
--   POST /api/settlement-progress-payments  (settlement_payment.sql)  a credit, then retries the waiting orders
-- Simulation only: modelled on public descriptions of DTC's settlement service, not affiliated with DTCC.
--
-- DVP = delivery versus payment: the units move from the deliverer to the receiver AND the amount
-- moves the other way, in the SAME transaction - both legs or neither. The money is not paid now:
-- it changes each side's NET settlement balance (deliverer +amount, receiver -amount); at the end of
-- the day each balance becomes one final figure, and each settling bank pays or receives one
-- net-net amount for all its participants (final_settlement.sql).
-- Before a delivery settles, the app checks the two risk controls on the LOCKED rows
-- (app/domain/risk.py); one that would break a control is not refused, it RECYCLES (waits):
--   Collateral Monitor (CM) = collateral_value_cents + net_cents   must stay >= 0
--   Net Debit Cap (NDC):      net_cents >= -net_debit_cap_cents
-- The same two rules are CHECKs on settlement_accounts, so even an app bug cannot commit a breach.
--
-- BEFORE any SQL here the APP has already:
--   * authenticated the participant and taken its account from the SESSION (never from the body)
--   * validated the body (else 400) and run the request's reserve_idempotency_key: this is a NEW
--     request, not a replay (a replay was answered with the stored result and never gets here)
--
-- PARAMETERS ARE NAMED. The engine builds them from what it locked and read:
--   locking:     {asset_id}
--   settling:    {submitted_operation_id, deliverer_account_id, receiver_account_id, asset_id,
--                 quantity, amount_cents} + settlement_operation_id (after the insert) + business_date
--   retrying:    {business_date}, then per order {instruction_id, settlement_operation_id}
--                or {instruction_id, recycle_reason}
--   Every _id, _cents and quantity value is a Python int; business_date is a Python date.
--
-- THE ONE GLOBAL LOCK ORDER (every flow that locks more than one of these takes them in this order,
-- so two requests can never each hold a lock the other one waits for - no deadlock):
--   settlement_day (the sequencer) -> positions (ORDER BY account_id) -> settlement_accounts (ORDER BY account_id)
-- The engine locks ALL positions and ALL settlement accounts up front, not only the two it needs:
--   a retry may settle ANY account's waiting order, and taking a lock in the middle of the
--   transaction could break the order above. With 4 accounts this is cheap; the trade-off is that
--   engine requests run one at a time. At DTC scale the work would be partitioned instead.
--
-- RULE: after EVERY UPDATE check cursor.rowcount == 1, otherwise raise (-> ROLLBACK, 500).
-- ERROR MAPPING (after ROLLBACK): CheckViolation 23514 on settlement_within_net_debit_cap
--   -> 422 "net debit cap exceeded"; on settlement_collateral_monitor_nonnegative -> 422
--   "collateral monitor would go negative" (the database backstop: the app's check missed a breach);
--   UniqueViolation on operations_actor_key_unique -> 409 (an order settled twice: a bug);
--   OperationalError (connection lost, lock_timeout) -> 503; anything else -> 500.

-- name: lock_settlement_day
-- The SEQUENCER: the one settlement_day row, locked FIRST by every request that can settle,
-- recycle or close (deliver order, payment, valued cutoff, final settlement). A second such
-- request WAITS here until the first one commits, so they run one at a time, in order - like one
-- settlement engine - and each one then reads the rows the previous one left behind.
-- No parameters: there is exactly one row (its CHECK allows only singleton = true).
SELECT business_date, phase, max_net_debit_cap_cents
FROM settlement_day
FOR UPDATE;
-- APP DECIDES:
--   not exactly 1 row -> 500 (the seed creates the row; it is updated in place, never deleted).
--   phase <> 'OPEN' -> 422 "valued processing is closed: the valued cutoff has passed"
--     (a lab choice: no delivery or payment after the valued cutoff).
--   business_date is the day every instruction and cash line of this request belongs to.

-- name: lock_engine_positions
-- Every account's position in this asset, locked in account_id order (the global lock order).
-- ORDER BY is applied below the row-locking step, so the rows are locked in this order.
-- In this lab a deliver order moves traditional units only (tokenized units move by transfer, Lesson 7).
SELECT account_id, traditional_units, tokenized_units
FROM positions
WHERE asset_id = %(asset_id)s
ORDER BY account_id
FOR UPDATE;
-- APP DECIDES: one row per account (the seed pre-creates them all). The app looks rows up by
--   account_id (never rows[0]); a settlement account without a position row -> 500.

-- name: lock_settlement_accounts
-- Every settlement account, locked in account_id order, AFTER the positions (the global lock order).
-- These are the numbers the two risk controls are checked against. Read AFTER the lock, so they
-- are the current values and cannot change before this transaction ends.
SELECT account_id, net_cents, collateral_value_cents, net_debit_cap_cents
FROM settlement_accounts
ORDER BY account_id
FOR UPDATE;
-- APP DECIDES: builds one AccountState per row (with that account's traditional_units from
--   lock_engine_positions) and evaluates every delivery against them (app/domain/risk.py).

-- name: read_collateral_price
-- What one unit is worth AS COLLATERAL (prior close less the haircut; a generated column).
-- No lock: no endpoint changes prices, so the value cannot change under us.
-- Demo: 10000 cents x (10000 - 500) / 10000 = 9500 cents per unit.
SELECT prior_close_cents, haircut_bps, collateral_cents_per_unit
FROM collateral_prices
WHERE asset_id = %(asset_id)s;
-- APP DECIDES: 0 rows -> 500 (the seed creates the price). Each delivered unit moves
--   collateral_cents_per_unit of collateral value from the deliverer to the receiver.

-- name: insert_dvp_settlement_operation
-- The settlement EVENT, a separate row from the DELIVER_ORDER request: a recycled order settles
-- later, inside somebody else's request (a payment, another order), so it has no actor of its own.
--   actor_user_id NULL       the system acted, not a user
--   idempotency_key          'dvp-' || the order's DELIVER_ORDER id, e.g. 'dvp-41' (|| joins text;
--                            Postgres turns the integer into text for it)
--   request_hash 'system'    nothing to compare: no client can send this key
-- A plain INSERT, NOT ON CONFLICT: operations_actor_key_unique is UNIQUE NULLS NOT DISTINCT, so a
-- second 'dvp-41' with actor NULL fails with UniqueViolation - one order can never settle twice.
INSERT INTO operations
  (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id,
   quantity, idempotency_key, request_hash, amount_cents)
VALUES
  ('DVP_SETTLEMENT', NULL, %(deliverer_account_id)s, %(receiver_account_id)s, %(asset_id)s,
   %(quantity)s, 'dvp-' || %(submitted_operation_id)s, 'system', %(amount_cents)s)
RETURNING id;
-- APP DECIDES: settlement_operation_id = row["id"]; every line below is written under it.
--   UniqueViolation -> the order was already settled: a bug, ROLLBACK (never a second settlement).

-- name: deliver_units
-- The SECURITIES leg, part 1: the deliverer gives q traditional units.
UPDATE positions
SET traditional_units = traditional_units - %(quantity)s
WHERE account_id = %(deliverer_account_id)s
  AND asset_id = %(asset_id)s
RETURNING traditional_units;
-- APP DECIDES: rowcount must be 1, otherwise raise. RETURNING gives the deliverer's new units.
-- Safety net: the CHECK (traditional_units >= 0) rejects this if the app's position check is wrong.

-- name: receive_units
-- The SECURITIES leg, part 2: the receiver gets the same q traditional units.
UPDATE positions
SET traditional_units = traditional_units + %(quantity)s
WHERE account_id = %(receiver_account_id)s
  AND asset_id = %(asset_id)s
RETURNING traditional_units;
-- APP DECIDES: rowcount must be 1, otherwise raise. RETURNING gives the receiver's new units.

-- name: credit_deliverer_settlement
-- The MONEY leg, part 1: the deliverer is owed the amount (net_cents goes UP), and the units it
-- gave away stop counting as its collateral. Both are RELATIVE updates (col = col + delta), so the
-- CHECKs judge the real new values, whatever the app thought they were.
-- The subquery reads the price per unit (one row per asset); q x price is the collateral that moves.
-- If the price row were missing, the subquery gives NULL, the new value is NULL, and NOT NULL fails.
UPDATE settlement_accounts
SET net_cents              = net_cents + %(amount_cents)s,
    collateral_value_cents = collateral_value_cents
                             - %(quantity)s * (SELECT collateral_cents_per_unit
                                               FROM collateral_prices
                                               WHERE asset_id = %(asset_id)s)
WHERE account_id = %(deliverer_account_id)s
RETURNING account_id, net_cents, collateral_value_cents, net_debit_cap_cents;
-- APP DECIDES: rowcount must be 1, otherwise raise. RETURNING refreshes the engine's copy of the account.
-- Safety net: settlement_collateral_monitor_nonnegative rejects a deliverer whose CM would go negative.

-- name: debit_receiver_settlement
-- The MONEY leg, part 2: the receiver owes the amount (net_cents goes DOWN), and the units it
-- received now count as its collateral - which is why a receiver with little collateral can
-- still take a delivery: the securities it receives help cover the debit.
UPDATE settlement_accounts
SET net_cents              = net_cents - %(amount_cents)s,
    collateral_value_cents = collateral_value_cents
                             + %(quantity)s * (SELECT collateral_cents_per_unit
                                               FROM collateral_prices
                                               WHERE asset_id = %(asset_id)s)
WHERE account_id = %(receiver_account_id)s
RETURNING account_id, net_cents, collateral_value_cents, net_debit_cap_cents;
-- APP DECIDES: rowcount must be 1, otherwise raise. RETURNING refreshes the engine's copy of the account.
-- Safety nets: settlement_within_net_debit_cap (debit beyond the cap) and
--   settlement_collateral_monitor_nonnegative (CM below zero) reject a breach the app missed.

-- name: insert_dvp_ledger_entries
-- The audit trail of the securities leg: two TRADITIONAL lines from the SAME quantity parameter,
-- -q for the deliverer and +q for the receiver, so they always net to zero.
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES
  (%(settlement_operation_id)s, %(deliverer_account_id)s, %(asset_id)s, 'TRADITIONAL', -%(quantity)s),
  (%(settlement_operation_id)s, %(receiver_account_id)s,  %(asset_id)s, 'TRADITIONAL',  %(quantity)s);
-- APP DECIDES: rowcount must be 2.

-- name: insert_dvp_cash_entries
-- The audit trail of the money leg: two PARTICIPANT_SETTLEMENT lines from the SAME amount,
-- +amount for the deliverer (credit) and -amount for the receiver (debit): they net to zero, and
-- each account's lines add up to its net_cents (reconciliation checks both).
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES
  (%(settlement_operation_id)s, %(business_date)s, 'PARTICIPANT_SETTLEMENT', %(deliverer_account_id)s,
   %(amount_cents)s),
  (%(settlement_operation_id)s, %(business_date)s, 'PARTICIPANT_SETTLEMENT', %(receiver_account_id)s,
   -%(amount_cents)s);
-- APP DECIDES: rowcount must be 2. The delivery has settled: units and money, in one transaction.

-- name: list_recycling_instructions
-- The waiting orders of today, read AFTER the sequencer is held: no other request can add, settle
-- or drop an order until this one commits, so this list cannot change under us (no FOR UPDATE needed).
-- ORDER (a lab choice): the largest amount first, then the oldest (lowest id) - deterministic, so
-- the same queue always settles the same way.
SELECT id, submitted_operation_id, deliverer_account_id, receiver_account_id, asset_id,
       quantity, amount_cents, recycle_reason
FROM delivery_instructions
WHERE status = 'RECYCLING'
  AND business_date = %(business_date)s
ORDER BY amount_cents DESC, id;
-- APP DECIDES, for each row in this order, against the engine's CURRENT copy of the accounts:
--   passes every control -> settle it (the statements above), then mark_instruction_settled.
--   still breaks one     -> keep it waiting; if the reason changed, update_recycle_reason.
--   One settlement can unlock another (a credit to its receiver), so the app reads this list
--   again and repeats until a whole pass settles nothing.
--   (A lab choice: retries happen only on credit events - an order that settles, a payment.)

-- name: mark_instruction_settled
-- A waiting order has just settled under settlement_operation_id.
-- AND status = 'RECYCLING': an order that is not waiting (already settled, or dropped at the
-- cutoff) is NOT changed - the rowcount rule then turns that into an error.
-- The CHECKs delivery_settled_has_operation and delivery_reason_matches_status require exactly
-- this shape: SETTLED with an operation and no reason.
UPDATE delivery_instructions
SET status                  = 'SETTLED',
    recycle_reason          = NULL,
    settlement_operation_id = %(settlement_operation_id)s
WHERE id = %(instruction_id)s
  AND status = 'RECYCLING';
-- APP DECIDES: rowcount must be 1, otherwise raise (-> ROLLBACK: the settlement above is undone too).

-- name: update_recycle_reason
-- A still-waiting order whose reason changed (e.g. the receiver's cap is no longer the problem,
-- but its collateral monitor is): keep the reason the participant sees current.
UPDATE delivery_instructions
SET recycle_reason = %(recycle_reason)s
WHERE id = %(instruction_id)s
  AND status = 'RECYCLING';
-- APP DECIDES: rowcount must be 1, otherwise raise. Run only when the reason actually changed.

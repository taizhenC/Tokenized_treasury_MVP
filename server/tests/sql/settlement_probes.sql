-- settlement_probes.sql   (Lessons 12-14 - SQL used only by the tests, never by the app)
-- The settlement tests (test_85, test_90, test_92, test_99) read the cash leg with these blocks:
-- what each account owes or is owed, what one request wrote, and which row locks a request waits on.
-- Same "-- name:" format as the app's query files. The older probes are in tests/sql/probes.sql.
--
-- BEFORE any SQL here the TEST has already:
--   * driven the app over HTTP (deliver orders, settlement progress payments, the valued cutoff,
--     final settlement) and received the answer: the app's transaction has COMMITTED or ROLLED BACK
--   * connected to the TEST database (tests/safety.py refuses the dev database)
--
-- ONE CONNECTION, AUTOCOMMIT: the probe connection runs each read on its own, outside every app
--   transaction, so it sees exactly what has been committed (READ COMMITTED).
--   Two exceptions: lock_settlement_day and lock_settlement_account_nowait run on a separate
--   connection that is NOT autocommit (tests.helpers.Blocker), so their row locks last until that
--   connection rolls back. That is how a test makes requests queue up at a known step.
--   And net_cents also runs INSIDE an app transaction, from a spy hook (tests/spy.py).
--
-- PARAMETERS ARE NAMED. A test passes only the names a block uses:
--   {account_id, operation_id, instruction_id, submitted_operation_id}
--   All are ids, sent as Python ints, so Postgres compares them as integers.
--   set_settlement_day alone takes {business_date, phase}: a Python date (sent as a DATE) and a text.
-- Asset 1 (DEMO-TREASURY) is the lab's only instrument, so the blocks name it directly.
-- In this file the "app" is the test suite: each "-- APP DECIDES:" note says what the test does
--   with the rows.

-- name: day_status
-- The settlement day in one row, plus the two counts that say whether the day is "flat":
-- no account owes or is owed money, and no deliver order is still waiting.
-- CROSS JOIN: settlement_day has exactly one row and the WHERE leaves one price row, so 1 x 1 = 1 row.
-- Each (SELECT count(*) ...) is a scalar subquery: it returns one number, used like a column.
SELECT d.business_date,
       d.phase,
       d.max_net_debit_cap_cents,
       c.collateral_cents_per_unit,
       (SELECT count(*) FROM settlement_accounts WHERE net_cents <> 0)          AS accounts_not_flat,
       (SELECT count(*) FROM delivery_instructions WHERE status = 'RECYCLING') AS recycling
FROM settlement_day d
CROSS JOIN collateral_prices c
WHERE c.asset_id = 1;
-- APP DECIDES: phase 'OPEN', accounts_not_flat 0 and recycling 0 -> a clean day to start from.
--   Anything else -> the test runs the valued cutoff and final settlement first (it "closes the day").

-- name: settlement_state
-- Per account: its units (positions) next to its money and collateral (settlement_accounts).
-- collateral_monitor_cents is computed here the same way DTC defines it: collateral + net.
SELECT p.account_id,
       p.traditional_units,
       p.tokenized_units,
       s.net_cents,
       s.collateral_value_cents,
       s.collateral_value_cents + s.net_cents AS collateral_monitor_cents,
       s.net_debit_cap_cents
FROM positions p
JOIN settlement_accounts s ON s.account_id = p.account_id
WHERE p.asset_id = 1
ORDER BY p.account_id;
-- APP DECIDES: the test builds {account_id: row} and computes every expected number from it with
--   the risk.py arithmetic, e.g. the receiver's CM after a delivery = CM + quantity x 9,500 - amount.

-- name: settlement_counts
-- How many rows the cash leg has written so far, one scalar subquery per table.
SELECT (SELECT count(*) FROM cash_ledger_entries)   AS cash_ledger_entries,
       (SELECT count(*) FROM delivery_instructions) AS delivery_instructions,
       (SELECT count(*) FROM settlement_figures)    AS settlement_figures,
       (SELECT count(*) FROM nss_file_lines)        AS nss_file_lines;
-- APP DECIDES: a refused, replayed or crashed request must leave all four numbers unchanged.

-- name: instruction_statuses
-- Every deliver order's current state, oldest first.
SELECT id, status, recycle_reason, settlement_operation_id
FROM delivery_instructions
ORDER BY id;
-- APP DECIDES: part of the "nothing changed" snapshot: a crashed retry must leave a waiting order
--   RECYCLING with its reason, exactly as it was.

-- name: instruction
-- One deliver order, every column: the EXACT recycle reason (never masked here).
SELECT id,
       submitted_operation_id,
       business_date,
       deliverer_account_id,
       receiver_account_id,
       asset_id,
       quantity,
       amount_cents,
       status,
       recycle_reason,
       settlement_operation_id
FROM delivery_instructions
WHERE id = %(instruction_id)s;
-- APP DECIDES: 1 row. The test compares status / recycle_reason / settlement_operation_id with what
--   the API answered (the API may show the counterparty WAITING_ON_COUNTERPARTY instead).

-- name: operation
-- One operations row: which type, who acted, which accounts, units and money, which key.
SELECT id,
       operation_type,
       actor_user_id,
       source_account_id,
       destination_account_id,
       asset_id,
       quantity,
       amount_cents,
       idempotency_key,
       request_hash
FROM operations
WHERE id = %(operation_id)s;
-- APP DECIDES: 1 row. A DVP_SETTLEMENT must have actor NULL (the system acted), key 'dvp-<id of
--   its DELIVER_ORDER>' and request_hash 'system'.

-- name: cash_lines_for_operation
-- The cash lines one operation wrote, in the order it wrote them.
SELECT business_date, ledger, account_id, settling_bank_id, amount_cents
FROM cash_ledger_entries
WHERE operation_id = %(operation_id)s
ORDER BY id;
-- APP DECIDES: a DVP settlement: deliverer +amount, receiver -amount. A payment: the payer +amount,
--   DTC's Fed account (account_id NULL, no bank) -amount. Final settlement: each account -net, each
--   settling bank's Fed line (settling_bank_id = that bank) +net-net. In every case the amounts add
--   up to 0.

-- name: cash_net_per_operation
-- The sum of each operation's cash lines. SUM of a BIGINT column is NUMERIC in Postgres (psycopg
-- would return a Decimal), so it is cast back to BIGINT: a Python int.
SELECT o.operation_type, c.operation_id, SUM(c.amount_cents)::bigint AS net
FROM cash_ledger_entries c
JOIN operations o ON o.id = c.operation_id
GROUP BY o.operation_type, c.operation_id
ORDER BY c.operation_id;
-- APP DECIDES: every net must be 0 - money only moves between ledgers, it is never created.

-- name: settlements_for_order
-- How many settlement events carry one order's SYSTEM key: 'dvp-' || the id of its DELIVER_ORDER.
-- || joins text; the integer parameter is turned into text for it, e.g. 'dvp-' || 41 = 'dvp-41'.
SELECT count(*) AS n
FROM operations
WHERE operation_type = 'DVP_SETTLEMENT'
  AND actor_user_id IS NULL
  AND idempotency_key = 'dvp-' || %(submitted_operation_id)s;
-- APP DECIDES: 0 (still waiting, or dropped) or 1 (settled). Never 2: the unique constraint
--   operations_actor_key_unique (NULLS NOT DISTINCT) refuses a second row with the same key.

-- name: net_cents
-- One account's settlement balance. Also run from a spy hook INSIDE an app transaction, where it
-- sees that transaction's own uncommitted change.
SELECT net_cents
FROM settlement_accounts
WHERE account_id = %(account_id)s;
-- APP DECIDES: inside the crashed request the debit is visible; on the probe connection it is not.

-- name: recycling_on_closed_days
-- Orders still RECYCLING on a day that has already closed (every closed day is before the open one).
SELECT count(*) AS n
FROM delivery_instructions
WHERE status = 'RECYCLING'
  AND business_date < (SELECT business_date FROM settlement_day);
-- APP DECIDES: must be 0 - the valued cutoff drops every order still waiting on its day.

-- name: set_settlement_day
-- Rolled-back tests only: put the sequencer row on a chosen date and phase, so the weekday rule of
-- open_next_business_day can be tried on every kind of day, whatever day the suite runs on.
UPDATE settlement_day
SET business_date = %(business_date)s,
    phase         = %(phase)s;
-- APP DECIDES: 1 row changed. The test then runs final_settlement.sql's open_next_business_day in the
--   same transaction and rolls everything back.

-- name: lock_settlement_day
-- Blocker only (a connection that is NOT autocommit): take the sequencer row and keep it.
-- Every deliver order, payment, cutoff and final settlement locks this row first, so they all
-- queue up behind it (visible in pg_stat_activity) until the blocker rolls back.
SELECT business_date
FROM settlement_day
FOR UPDATE;
-- APP DECIDES: the test starts requests, waits until they are all waiting on this lock, then
--   releases it and lets them run one at a time, in queue order.

-- name: lock_settlement_account_nowait
-- One settlement account, locked WITHOUT waiting: NOWAIT fails at once with 55P03
-- (LockNotAvailable) if another transaction holds the row.
SELECT account_id
FROM settlement_accounts
WHERE account_id = %(account_id)s
FOR UPDATE NOWAIT;
-- APP DECIDES: success proves that a request queued earlier in the lock order (sequencer ->
--   positions -> settlement accounts) has NOT locked its settlement accounts yet.

-- probes.sql - SQL used only by the tests (state checks, locks, schema facts).
-- Same "-- name:" format as the app's query files.

-- name: positions
SELECT account_id, traditional_units, tokenized_units
FROM positions
ORDER BY account_id;

-- name: counts
-- Everything a rejected request must leave unchanged (with positions): row counts, and since
-- Lesson 11 every settlement balance ("101:net/collateral ...") and the day's date and phase.
SELECT (SELECT count(*) FROM operations) AS operations,
       (SELECT count(*) FROM ledger_entries) AS ledger_entries,
       (SELECT count(*) FROM delivery_instructions) AS delivery_instructions,
       (SELECT count(*) FROM cash_ledger_entries) AS cash_ledger_entries,
       (SELECT string_agg(account_id || ':' || net_cents || '/' || collateral_value_cents, ' ' ORDER BY account_id)
        FROM settlement_accounts) AS settlement_accounts,
       (SELECT business_date || ' ' || phase FROM settlement_day) AS settlement_day;

-- name: tokenized_units
SELECT tokenized_units
FROM positions
WHERE account_id = %(account_id)s AND asset_id = 1;

-- name: any_operation_id
SELECT min(id) AS id FROM operations;

-- name: traditional_units
SELECT traditional_units
FROM positions
WHERE account_id = %(account_id)s AND asset_id = 1;

-- name: rows_for_key
SELECT count(*) AS n
FROM operations
WHERE actor_user_id = %(actor_user_id)s AND idempotency_key = %(idempotency_key)s;

-- name: ledger_lines_for_operation
SELECT account_id, representation, units_delta
FROM ledger_entries
WHERE operation_id = %(operation_id)s
ORDER BY id;

-- name: ledger_net_per_operation
SELECT o.operation_type, l.operation_id, SUM(l.units_delta) AS net
FROM ledger_entries l
JOIN operations o ON o.id = l.operation_id
GROUP BY o.operation_type, l.operation_id
ORDER BY l.operation_id;

-- name: ledger_apply_order
SELECT operation_id, max(id) AS last_line
FROM ledger_entries
WHERE account_id = %(account_id)s AND operation_id = ANY(%(operation_ids)s)
GROUP BY operation_id
ORDER BY last_line;

-- name: keys_like
SELECT count(*) AS n
FROM operations
WHERE idempotency_key LIKE %(pattern)s OR length(idempotency_key) > 100;

-- name: lock_waiters
SELECT pid, query
FROM pg_stat_activity
WHERE datname = current_database()
  AND wait_event_type = 'Lock'
  AND position(%(needle)s in query) > 0
ORDER BY pid;

-- name: terminate_backend
SELECT pg_terminate_backend(%(pid)s) AS terminated;

-- name: lock_position_row
SELECT account_id
FROM positions
WHERE account_id = %(account_id)s AND asset_id = 1
FOR UPDATE;

-- name: lock_position_row_nowait
SELECT account_id
FROM positions
WHERE account_id = %(account_id)s AND asset_id = 1
FOR UPDATE NOWAIT;

-- name: lock_ledger_against_writes
LOCK TABLE ledger_entries IN SHARE MODE;

-- name: set_approval
UPDATE accounts
SET is_approved = %(is_approved)s
WHERE id = %(account_id)s;

-- name: transaction_settings
SELECT current_setting('transaction_isolation') AS isolation,
       current_setting('transaction_read_only') AS read_only;

-- name: touch_positions
UPDATE positions SET tokenized_units = tokenized_units WHERE account_id = 101;

-- name: no_op
SELECT 1;

-- name: analyze_positions
ANALYZE positions;

-- name: disable_index_scan
SET enable_indexscan = off;

-- name: disable_bitmap_scan
SET enable_bitmapscan = off;

-- name: types_of_aggregates
SELECT count(*) AS n, SUM(units_delta) AS s, SUM(units_delta)::integer AS si, AVG(units_delta) AS a
FROM ledger_entries;

-- name: create_scratch_table
CREATE TEMP TABLE scratch (x integer);

-- name: insert_scratch
INSERT INTO scratch (x) VALUES (%(x)s);

-- name: count_scratch
SELECT count(*) AS n FROM scratch;

-- name: raw_commit
COMMIT;

-- name: schema_migrations
SELECT filename, applied_at IS NOT NULL AS has_time
FROM schema_migrations
ORDER BY filename;

-- name: tables
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;

-- name: constraints
SELECT conrelid::regclass::text AS table_name, conname
FROM pg_constraint
WHERE connamespace = 'public'::regnamespace
ORDER BY 1, 2;

-- name: columns
SELECT column_name, data_type, is_nullable, column_default, is_identity, identity_generation,
       is_generated, generation_expression
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %(table_name)s
ORDER BY ordinal_position;

-- name: table_exists
SELECT to_regclass(%(name)s) IS NOT NULL AS exists;

-- name: accounts
SELECT id, is_approved FROM accounts ORDER BY id;

-- name: users
SELECT id, username, role, account_id FROM users ORDER BY id;

-- name: password_hashes
SELECT DISTINCT password_hash FROM users;

-- name: operations
SELECT operation_type, actor_user_id, source_account_id, destination_account_id, quantity, idempotency_key,
       amount_cents
FROM operations
ORDER BY id;

-- name: ledger_entries
SELECT account_id, representation, units_delta FROM ledger_entries ORDER BY id;

-- name: ledger_sum_per_account
SELECT account_id, SUM(units_delta) AS total
FROM ledger_entries
GROUP BY account_id
ORDER BY account_id;

-- name: held_total
SELECT SUM(traditional_units + tokenized_units) AS held FROM positions;

-- name: terminate_other_connections
-- A database restart, as the app sees it: every OTHER client connection to this database (the
-- live server's pooled connections) is terminated. Waits up to 5 s for each one to exit.
SELECT count(*) FILTER (WHERE pg_terminate_backend(pid, 5000)) AS terminated
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND backend_type = 'client backend';


-- ---------- Lesson 11: the cash leg ----------

-- name: money_columns
-- Every column whose name ends in _cents, in every table: money must be whole cents as BIGINT.
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public' AND right(column_name, 6) = '_cents'
ORDER BY table_name, column_name;

-- name: unique_constraint_nulls
-- How a UNIQUE constraint compares NULLs: nulls_not_distinct = true means two NULLs count as equal.
SELECT pg_get_constraintdef(c.oid) AS definition, i.indnullsnotdistinct AS nulls_not_distinct
FROM pg_constraint c
JOIN pg_index i ON i.indexrelid = c.conindid
WHERE c.connamespace = 'public'::regnamespace AND c.conname = %(name)s;

-- name: settling_banks
SELECT a.id AS account_id, a.settling_bank_id, b.bank_name
FROM accounts a
LEFT JOIN settling_banks b ON b.id = a.settling_bank_id
ORDER BY a.id;

-- name: settlement_day
-- is_seed_day: the business date is the first weekday on or after the day seed 004 ran (Saturday
-- +2, Sunday +1, a weekday +0, like the seed). The day comes from the runner's own record, applied_at,
-- not from today: the runner applies a file and records it in ONE transaction, so applied_at (now())
-- and the seed's CURRENT_DATE share that transaction's start time, read in the same time zone.
SELECT d.business_date,
       d.phase,
       d.max_net_debit_cap_cents,
       d.business_date = m.applied_at::date + CASE EXTRACT(ISODOW FROM m.applied_at::date)
                                                 WHEN 6 THEN 2
                                                 WHEN 7 THEN 1
                                                 ELSE 0
                                               END AS is_seed_day
FROM settlement_day d
CROSS JOIN schema_migrations m
WHERE m.filename = 'seeds/004_settlement_reference.sql';

-- name: collateral_prices
SELECT asset_id, prior_close_cents, haircut_bps, collateral_cents_per_unit
FROM collateral_prices
ORDER BY asset_id;

-- name: net_debit_cap_inputs
SELECT account_id, avg_top3_peak_cents, factor_bps
FROM net_debit_cap_inputs
ORDER BY account_id;

-- name: settlement_accounts
SELECT account_id, net_cents, collateral_value_cents, net_debit_cap_cents
FROM settlement_accounts
ORDER BY account_id;

-- name: types_of_money_values
-- SUM of a BIGINT column is NUMERIC (-> Decimal) unless cast back; a DATE arrives as datetime.date.
SELECT SUM(net_debit_cap_cents) AS s, SUM(net_debit_cap_cents)::bigint AS sb,
       (SELECT business_date FROM settlement_day) AS d
FROM settlement_accounts;

-- name: mint_before_lesson_11
-- test_15 only, on a database from BEFORE migration 004: a mint the way Lessons 6 and 8 wrote it
-- (operation, position update, two ledger lines), squeezed into ONE statement with data-modifying
-- CTEs. Each CTE runs once; the final INSERT writes both ledger lines for the new operation.
WITH op AS (
  INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
  VALUES ('CONVERT_TO_TOKENIZED', %(actor_user_id)s, %(account_id)s, 1, %(quantity)s, %(idempotency_key)s, 'before-lesson-11')
  RETURNING id
), moved AS (
  UPDATE positions
  SET traditional_units = traditional_units - %(quantity)s,
      tokenized_units = tokenized_units + %(quantity)s
  WHERE account_id = %(account_id)s AND asset_id = 1
  RETURNING account_id
)
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
SELECT op.id, moved.account_id, 1, line.representation, line.units_delta
FROM op, moved, (VALUES ('TRADITIONAL', -%(quantity)s), ('TOKENIZED', %(quantity)s)) AS line (representation, units_delta);

-- name: plant_instruction_beta_to_gamma
-- test_45 only: a deliver order from beta (102) to gamma (103) that alpha must never see, written
-- directly - no request runs it, so no balance changes. DROPPED_AT_CUTOFF on a past date, so no
-- retry or cutoff can ever pick it up. remove_planted_instruction deletes both rows again.
WITH submitted AS (
  INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
  VALUES ('DELIVER_ORDER', 12, 102, 103, 1, 1, 100, %(idempotency_key)s, 'planted')
  RETURNING id
)
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
SELECT id, DATE '2000-01-03', 102, 103, 1, 1, 100, 'DROPPED_AT_CUTOFF', 'RECEIVER_NET_DEBIT_CAP'
FROM submitted
RETURNING id;

-- name: remove_planted_instruction
-- The instruction first, then its DELIVER_ORDER row: both in one statement, so the foreign key
-- (checked at the end of the statement) sees neither.
WITH removed AS (
  DELETE FROM delivery_instructions WHERE id = %(instruction_id)s RETURNING submitted_operation_id
)
DELETE FROM operations WHERE id IN (SELECT submitted_operation_id FROM removed);

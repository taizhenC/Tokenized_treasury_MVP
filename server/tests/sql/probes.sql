-- probes.sql - SQL used only by the tests (state checks, locks, schema facts).
-- Same "-- name:" format as the app's query files.

-- name: positions
SELECT account_id, traditional_units, tokenized_units
FROM positions
ORDER BY account_id;

-- name: counts
SELECT (SELECT count(*) FROM operations) AS operations,
       (SELECT count(*) FROM ledger_entries) AS ledger_entries;

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
SELECT column_name, data_type, is_nullable, column_default, is_identity, identity_generation
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
SELECT operation_type, actor_user_id, source_account_id, destination_account_id, quantity, idempotency_key
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

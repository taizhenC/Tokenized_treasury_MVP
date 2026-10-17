-- reconciliation.sql   (Lesson 9 - GET /api/reconciliation) - read-only report, no parameters.
-- The app runs all three statements on ONE connection inside  with conn.transaction():
--   psycopg sends BEGIN, then set_snapshot MUST be the first statement of that transaction,
--   then the two checks, then COMMIT.
-- REPEATABLE READ: both checks read the SAME snapshot, so a transfer that commits between
--   check (1) and check (2) cannot make them disagree.
-- READ ONLY: Postgres refuses any write inside this transaction.
-- SET TRANSACTION changes only THIS transaction. The pooled connection goes back to the pool at
--   the default (READ COMMITTED, read/write), which is what the write flows need.
-- SUM(...) of an INTEGER column is BIGINT. psycopg would return it as an exact Python int, but
--   every displayed total is still cast back with ::integer:
--     * the React client is JavaScript, and an INTEGER always fits a JS number exactly;
--     * a total that no longer fits INTEGER raises error 22003 - loud, never a wrong answer.
--   Every row's "ok" boolean is computed IN SQL.
-- APP DECIDES: the report passes only if every row of BOTH checks has ok = true.
--   On any error psycopg sends ROLLBACK instead of COMMIT.
--
-- Pipe it into psql as ONE transaction (-1), so set_snapshot is the first statement:
--   psql -1 -v ON_ERROR_STOP=1 -f server/db/queries/reconciliation.sql
-- (Without -1, psql warns "SET TRANSACTION can only be used in transaction blocks" and each
--  check runs in its own snapshot.)
--
-- What this report PROVES: units are conserved (1) and every balance equals its ledger history (2).
-- What it CANNOT prove: that the RIGHT account received the units. A transfer credited to the
--   wrong account (or made by the wrong actor, or to an unapproved wallet), written consistently
--   to both the ledger and the positions, passes both checks. That is PREVENTED by the transaction
--   code and authorization, not DETECTED here.

-- name: set_snapshot
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;

-- name: check_conservation
-- Check (1), per asset: units held by all accounts, in both representations, = units in custody.
-- Conversion moves units between columns and transfer moves them between rows;
-- neither may change this sum.
SELECT s.symbol,
       s.total_custodied_units,
       COALESCE(SUM(p.traditional_units), 0)::integer                     AS traditional_total,
       COALESCE(SUM(p.tokenized_units), 0)::integer                       AS tokenized_total,
       COALESCE(SUM(p.traditional_units + p.tokenized_units), 0)::integer AS held_total,
       COALESCE(SUM(p.traditional_units + p.tokenized_units), 0)
         = s.total_custodied_units                                        AS ok
FROM assets s
LEFT JOIN positions p ON p.asset_id = s.id
GROUP BY s.id, s.symbol, s.total_custodied_units
ORDER BY s.symbol;

-- name: check_positions_vs_ledger
-- Check (2), per account / asset / representation: stored balance = SUM of its ledger lines.
--   stored: turns each position row into two rows, one per representation (UNION ALL)
--   ledger: adds up the ledger lines per bucket (GROUP BY)
-- LEFT JOIN from stored is enough: ledger_entries_position_fk (the position exists) plus the
-- representation CHECK (only these two names) guarantee every ledger line has a "stored" bucket.
-- A bucket with no lines yet (Delta) counts as 0 (COALESCE).
WITH stored AS (
  SELECT account_id, asset_id, 'TRADITIONAL' AS representation, traditional_units AS stored_units
  FROM positions
  UNION ALL
  SELECT account_id, asset_id, 'TOKENIZED', tokenized_units
  FROM positions
),
ledger AS (
  SELECT account_id, asset_id, representation, SUM(units_delta)::integer AS ledger_units
  FROM ledger_entries
  GROUP BY account_id, asset_id, representation
)
SELECT st.account_id,
       st.asset_id,
       st.representation,
       st.stored_units,
       COALESCE(l.ledger_units, 0)                   AS ledger_units,
       st.stored_units = COALESCE(l.ledger_units, 0) AS ok
FROM stored st
LEFT JOIN ledger l
       ON l.account_id     = st.account_id
      AND l.asset_id       = st.asset_id
      AND l.representation = st.representation
ORDER BY st.account_id, st.asset_id, st.representation;

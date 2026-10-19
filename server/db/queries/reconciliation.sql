-- reconciliation.sql   (Lesson 9 - GET /api/reconciliation; six settlement checks added in Lesson 14)
-- Read-only report, no parameters.
-- The app runs all nine statements on ONE connection inside  with conn.transaction():
--   psycopg sends BEGIN, then set_snapshot MUST be the first statement of that transaction,
--   then the eight checks, then COMMIT.
-- REPEATABLE READ: every check reads the SAME snapshot, so an operation that commits between two
--   checks (a transfer, a deliver order, a final settlement) cannot make them disagree.
-- READ ONLY: Postgres refuses any write inside this transaction.
-- SET TRANSACTION changes only THIS transaction. The pooled connection goes back to the pool at
--   the default (READ COMMITTED, read/write), which is what the write flows need.
-- SUM(...) of an INTEGER column is BIGINT. psycopg would return it as an exact Python int, but
--   every displayed total of units (and every COUNT) is still cast back with ::integer:
--     * the React client is JavaScript, and an INTEGER always fits a JS number exactly;
--     * a total that no longer fits INTEGER raises error 22003 - loud, never a wrong answer.
-- SUM(...) of a BIGINT column (every *_cents column) is NUMERIC, which psycopg returns as a Python
--   Decimal (and FastAPI would send as a float). Every money total is cast back with ::bigint:
--   exact whole cents, and a total too big for BIGINT raises 22003 instead of rounding.
--   Every row's "ok" boolean is computed IN SQL.
-- APP DECIDES: the report passes only if every row of EVERY check has ok = true. A check with no
--   rows (no closed day yet) has no failing row, so it passes; checks (3) and (6) always return
--   exactly one summary row, also on a fresh database with no cash lines and no deliver orders.
--   Checks (4), (5) and (7) start FROM accounts, so a missing settlement account (or a missing
--   settlement_day row) is a failing row, never a row that is simply not there.
--   On any error psycopg sends ROLLBACK instead of COMMIT.
--
-- Pipe it into psql as ONE transaction (-1), so set_snapshot is the first statement:
--   psql -1 -v ON_ERROR_STOP=1 -f server/db/queries/reconciliation.sql
-- (Without -1, psql warns "SET TRANSACTION can only be used in transaction blocks" and each
--  check runs in its own snapshot.)
--
-- What this report PROVES: units are conserved (1) and every balance equals its ledger history (2);
--   every operation's cash lines balance (3) and every account has a settlement balance that equals
--   its cash history (4); every account's collateral matches its positions (5); every settled
--   deliver order moved exactly what it instructed, between exactly the accounts it named (6); the
--   settlement day exists, and the caps and both risk controls hold (7); and for every closed day
--   (8): each account's final figure is what was closed on its balance, each settling bank's NSS
--   line and end-of-day Fed line equal the sum of the figures of the accounts it settles for, all
--   figures add up to that day's settlement progress payments, and DTC's Fed account ends at 0.
-- What it CANNOT prove: that the RIGHT account received the units of a TRANSFER. A transfer
--   credited to the wrong account (or made by the wrong actor, or to an unapproved wallet), written
--   consistently to both the ledger and the positions, passes checks (1) and (2). That is PREVENTED
--   by the transaction code and authorization, not DETECTED here.
--   Lesson 14 closes this gap PARTLY, for DVP only: delivery_instructions records each instruction
--   apart from the ledgers, so check (6) catches a settlement that moved units or money to an
--   account (or in an amount) the order did not name. Transfers still have no such record.

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

-- name: check_cash_lines_net_to_zero
-- Check (3), one summary row: every operation's cash lines add up to ZERO - money only MOVES:
--   a DVP settlement           deliverer +amount, receiver -amount
--   a settlement payment       the participant +amount, DTC's Fed account -amount
--   a final settlement         each account -its net, each settling bank's Fed line +its net-net
-- per_operation adds up the lines of each operation (GROUP BY). The outer SELECT has no GROUP BY,
-- so it returns ONE row even when there are no cash lines at all (COUNT of nothing is 0, ok true).
-- COUNT(*) FILTER (WHERE ...) counts only the rows that meet the condition.
WITH per_operation AS (
  SELECT operation_id, SUM(amount_cents)::bigint AS total_cents
  FROM cash_ledger_entries
  GROUP BY operation_id
)
SELECT COUNT(*)::integer                                 AS operations_checked,
       COUNT(*) FILTER (WHERE total_cents <> 0)::integer AS failing,
       COUNT(*) FILTER (WHERE total_cents <> 0) = 0      AS ok
FROM per_operation;

-- name: check_settlement_vs_cash_ledger
-- Check (4), per account: stored net_cents = SUM of its PARTICIPANT_SETTLEMENT lines.
-- The cash twin of check (2). No opening line is needed: seed 004 starts every account at 0.
-- Every day counts: final settlement posts each account's -net on the day it closes, so a closed
-- day's lines add up to 0 and only the open day's lines are left in the sum.
-- FROM accounts, not FROM settlement_accounts: every account must HAVE a settlement account (seed
--   004 creates one per account; without it the engine could not settle that account's orders).
--   LEFT JOIN settlement_accounts keeps an account that has none, with NULL figures, and
--   "sa.account_id IS NOT NULL AND ..." makes its ok FALSE (FALSE AND anything is FALSE, even
--   FALSE AND NULL): it fails, instead of silently dropping out of the check.
-- LEFT JOIN ledger is enough for the lines: a PARTICIPANT_SETTLEMENT line must name an account
-- (cash_line_shape), and that account must be a settlement account (foreign key), so every such
-- line has a row here. An account with no lines yet (Delta) counts as 0 (COALESCE).
WITH ledger AS (
  SELECT account_id, SUM(amount_cents)::bigint AS ledger_net_cents
  FROM cash_ledger_entries
  WHERE ledger = 'PARTICIPANT_SETTLEMENT'
  GROUP BY account_id
)
SELECT a.id                            AS account_id,
       sa.net_cents                    AS stored_net_cents,
       COALESCE(l.ledger_net_cents, 0) AS ledger_net_cents,
       sa.account_id IS NOT NULL
         AND sa.net_cents = COALESCE(l.ledger_net_cents, 0) AS ok
FROM accounts a
LEFT JOIN settlement_accounts sa ON sa.account_id = a.id
LEFT JOIN ledger l               ON l.account_id  = a.id
ORDER BY a.id;

-- name: check_collateral_vs_positions
-- Check (5), per account: stored collateral = what its positions are worth as collateral,
--   SUM(traditional_units x collateral_cents_per_unit). Tokenized units count ZERO: the no-action
--   letter's rule (DTC gives tokenized entitlements no collateral value), written as a check.
-- It catches a mint, burn or delivery that moved units but forgot (or doubled) the collateral update.
-- held: one row per account, adding up its positions. This lab has one instrument, so one position
--   per account; the SUM would still give the right total with several. MAX of that one price is
--   the price itself: an aggregate function is needed only because of the GROUP BY.
-- LEFT JOIN collateral_prices: a position without a price still counts in the units and adds
--   nothing to the expected collateral (SUM skips the NULL product).
-- FROM accounts + LEFT JOIN settlement_accounts, like check (4): an account without a settlement
--   account gets a NULL stored collateral and fails (ok FALSE) instead of dropping out.
-- LEFT JOIN held + COALESCE: an account without a position row expects 0.
-- traditional_units (INTEGER) x collateral_cents_per_unit (BIGINT) is BIGINT; its SUM needs ::bigint.
WITH held AS (
  SELECT p.account_id,
         SUM(p.traditional_units)::integer                              AS traditional_units,
         SUM(p.tokenized_units)::integer                                AS tokenized_units,
         MAX(c.collateral_cents_per_unit)                               AS collateral_cents_per_unit,
         SUM(p.traditional_units * c.collateral_cents_per_unit)::bigint AS expected_collateral_cents
  FROM positions p
  LEFT JOIN collateral_prices c ON c.asset_id = p.asset_id
  GROUP BY p.account_id
)
SELECT a.id                                     AS account_id,
       COALESCE(h.traditional_units, 0)         AS traditional_units,
       COALESCE(h.tokenized_units, 0)           AS tokenized_units,
       h.collateral_cents_per_unit,
       sa.collateral_value_cents                AS stored_collateral_cents,
       COALESCE(h.expected_collateral_cents, 0) AS expected_collateral_cents,
       sa.account_id IS NOT NULL
         AND sa.collateral_value_cents = COALESCE(h.expected_collateral_cents, 0) AS ok
FROM accounts a
LEFT JOIN settlement_accounts sa ON sa.account_id = a.id
LEFT JOIN held h                 ON h.account_id  = a.id
ORDER BY a.id;

-- name: check_settled_instructions_vs_ledgers
-- Check (6), one summary row: every SETTLED deliver order against what was really written for it.
-- For each settled order, its DVP_SETTLEMENT operation must match it (type, deliverer = source,
-- receiver = destination, asset, quantity, amount), and that operation must have EXACTLY
--   2 unit lines:  the deliverer's TRADITIONAL -quantity and the receiver's TRADITIONAL +quantity
--   2 cash lines:  the deliverer's PARTICIPANT_SETTLEMENT +amount and the receiver's -amount
-- And the other way round: no DVP_SETTLEMENT operation may exist without an order that points at it
-- (an "orphan" settlement moved units and money that nobody instructed).
-- This is what checks (1)-(4) cannot see: units and money written CONSISTENTLY to the wrong account
-- balance every ledger, but no longer match the instruction.
--
-- settled: each settled order next to its operation. operation_matches is one boolean: every
--   column pair must be equal. LEFT JOIN: an order whose operation row were missing still appears
--   (operation_matches is then NULL), so it fails instead of disappearing.
-- unit_lines / cash_lines: per settled order, COUNT(column) counts the lines that exist (a LEFT JOIN
--   that finds no line gives one row of NULLs, counted as 0), and COUNT(...) FILTER (WHERE ...)
--   counts only the lines that are exactly the expected one. 2 lines in all and 1 of each kind:
--   nothing missing, nothing extra, no line counted twice.
-- checked: one boolean per order. failing counts "matches IS NOT TRUE", so a NULL (unknown) counts
--   as a failure, never as a pass.
-- summary and orphans are aggregates without GROUP BY: one row each, also with no orders at all.
--   CROSS JOIN of 1 row x 1 row = the one result row.
WITH settled AS (
  SELECT di.id,
         di.settlement_operation_id,
         di.deliverer_account_id,
         di.receiver_account_id,
         di.asset_id,
         di.quantity,
         di.amount_cents,
         o.operation_type = 'DVP_SETTLEMENT'
           AND o.source_account_id      = di.deliverer_account_id
           AND o.destination_account_id = di.receiver_account_id
           AND o.asset_id               = di.asset_id
           AND o.quantity               = di.quantity
           AND o.amount_cents           = di.amount_cents AS operation_matches
  FROM delivery_instructions di
  LEFT JOIN operations o ON o.id = di.settlement_operation_id
  WHERE di.status = 'SETTLED'
),
unit_lines AS (
  SELECT s.id,
         COUNT(le.id) AS all_lines,
         COUNT(le.id) FILTER (WHERE le.account_id     = s.deliverer_account_id
                                AND le.asset_id       = s.asset_id
                                AND le.representation = 'TRADITIONAL'
                                AND le.units_delta    = -s.quantity) AS deliverer_lines,
         COUNT(le.id) FILTER (WHERE le.account_id     = s.receiver_account_id
                                AND le.asset_id       = s.asset_id
                                AND le.representation = 'TRADITIONAL'
                                AND le.units_delta    = s.quantity)  AS receiver_lines
  FROM settled s
  LEFT JOIN ledger_entries le ON le.operation_id = s.settlement_operation_id
  GROUP BY s.id
),
cash_lines AS (
  SELECT s.id,
         COUNT(c.id) AS all_lines,
         COUNT(c.id) FILTER (WHERE c.ledger       = 'PARTICIPANT_SETTLEMENT'
                               AND c.account_id   = s.deliverer_account_id
                               AND c.amount_cents = s.amount_cents)  AS deliverer_lines,
         COUNT(c.id) FILTER (WHERE c.ledger       = 'PARTICIPANT_SETTLEMENT'
                               AND c.account_id   = s.receiver_account_id
                               AND c.amount_cents = -s.amount_cents) AS receiver_lines
  FROM settled s
  LEFT JOIN cash_ledger_entries c ON c.operation_id = s.settlement_operation_id
  GROUP BY s.id
),
checked AS (
  SELECT s.id,
         s.operation_matches
           AND u.all_lines = 2 AND u.deliverer_lines = 1 AND u.receiver_lines = 1
           AND c.all_lines = 2 AND c.deliverer_lines = 1 AND c.receiver_lines = 1 AS matches
  FROM settled s
  JOIN unit_lines u ON u.id = s.id
  JOIN cash_lines c ON c.id = s.id
),
summary AS (
  SELECT COUNT(*)::integer                                    AS instructions_checked,
         COUNT(*) FILTER (WHERE matches IS NOT TRUE)::integer AS failing
  FROM checked
),
orphans AS (
  SELECT COUNT(*)::integer AS orphan_settlements
  FROM operations o
  WHERE o.operation_type = 'DVP_SETTLEMENT'
    AND NOT EXISTS (SELECT 1
                    FROM delivery_instructions di
                    WHERE di.settlement_operation_id = o.id)
)
SELECT s.instructions_checked,
       s.failing,
       o.orphan_settlements,
       s.failing = 0 AND o.orphan_settlements = 0 AS ok
FROM summary s
CROSS JOIN orphans o;

-- name: check_risk_controls
-- Check (7), per account: the stored cap is the cap its inputs give, and both risk controls hold
-- right now:
--   expected cap        LEAST(DTC's maximum, average of the 3 highest peaks x factor) - the seed's
--                       formula; BIGINT / 10000 truncates (rounds down) like the seed and risk.py
--   net debit cap       net_cents >= -cap
--   collateral monitor  collateral + net >= 0
-- The two controls are also CHECKs on settlement_accounts, so they can fail here only if those
-- constraints were dropped. The cap comparison catches a cap typed in by hand, or a stale one after
-- its inputs or DTC's maximum changed.
-- caps: one row per account with everything ok needs; the outer SELECT then reads like the rule.
-- Three rows must EXIST for the rule to mean anything, so ok requires each one (FALSE if missing):
--   has_settlement_account  the row the controls guard (FROM accounts + LEFT JOIN, like (4) and (5))
--   has_inputs              its cap inputs (LEFT JOIN: an account without them still appears)
--   has_day                 the settlement_day row, which holds DTC's maximum cap. Without it every
--                           deliver order, payment, cutoff and final settlement fails, so this must too.
-- Why the flags are needed: LEAST ignores NULL arguments in Postgres. Without inputs,
--   expected_cap_cents would be the maximum; without the day row it would be average x factor -
--   which equals the stored cap of every account below the maximum, so the cap comparison alone
--   would still pass.
-- LEFT JOIN settlement_day d ON true: its one row (the maximum) is added to every account row
--   (N x 1 = N rows). ON true joins every account row to it, and LEFT keeps the account rows (with
--   d.* NULL) even if the day row is missing. The net and CM comparisons are still made for every
--   account, so a breach still shows in the row's numbers.
WITH caps AS (
  SELECT a.id                                                                         AS account_id,
         sa.net_cents,
         sa.net_debit_cap_cents,
         LEAST(d.max_net_debit_cap_cents, n.avg_top3_peak_cents * n.factor_bps / 10000) AS expected_cap_cents,
         sa.collateral_value_cents + sa.net_cents                                     AS collateral_monitor_cents,
         sa.account_id IS NOT NULL                                                    AS has_settlement_account,
         n.account_id IS NOT NULL                                                     AS has_inputs,
         d.singleton IS NOT NULL                                                      AS has_day
  FROM accounts a
  LEFT JOIN settlement_accounts sa ON sa.account_id = a.id
  LEFT JOIN net_debit_cap_inputs n ON n.account_id  = a.id
  LEFT JOIN settlement_day d       ON true
)
SELECT account_id,
       net_cents,
       net_debit_cap_cents,
       expected_cap_cents,
       collateral_monitor_cents,
       has_settlement_account
         AND has_inputs
         AND has_day
         AND net_debit_cap_cents = expected_cap_cents
         AND net_cents >= -net_debit_cap_cents
         AND collateral_monitor_cents >= 0 AS ok
FROM caps
ORDER BY account_id;

-- name: check_closed_days
-- Check (8), per closed day (a day that final settlement has closed): the day's money must agree
-- in total AND line by line.
--   figures_total         SUM of every account's final figure
--   nss_total             SUM of the NSS file's net-net lines (one per settling bank)
--   payments_total        the day's settlement progress payments (their participant lines)
--   fed_total             SUM of the day's lines on DTC's Fed account
--   accounts_mismatched   accounts whose final figure is not what final settlement closed on its
--                         balance: its closing PARTICIPANT_SETTLEMENT line must be -figure
--                         (a 0 figure has no line)
--   banks_mismatched      settling banks whose NSS line is not the SUM of the figures of the
--                         accounts it settles for, or whose closing Fed line is not that NSS line
--                         (a bank with a 0 net-net has no Fed line)
-- Why the totals must agree: a DVP settlement only moves money BETWEEN participants (+amount,
-- -amount), so at the end of the day all the nets together add up to exactly the money paid in that
-- day. And DTC's Fed account ends the day at 0: each payment came in (-amount), and the banks'
-- end-of-day lines (+net-net) send it all back out.
-- Why the totals are not enough: the NSS net-net is the figure that actually moves money at the
-- Fed, and two banks' lines shifted +X / -X (or two accounts' figures) leave every total unchanged.
-- So each bank's NSS line and Fed line are compared with the figures they were built from, and each
-- figure with the closing line posted for it.
-- ok = figures = NSS = payments, the Fed lines add up to 0, and no account and no bank mismatches.
--
-- figures / nss / payments / fed: one total per day (GROUP BY business_date).
-- closing_lines: what final settlement posted to each account's balance on the day it closed.
-- bank_figures: the net-net each bank SHOULD have, from the figures of its accounts.
--   Lab simplification: an account's settling bank never changes here, so today's
--   accounts.settling_bank_id is also its bank on every past day. An account with no bank gives a
--   row with a NULL bank, which no NSS line can match, so its day fails here, loudly.
-- bank_fed: each bank's closing line on DTC's Fed account (final settlement writes one per
--   non-zero NSS line, naming the bank in settling_bank_id).
-- closed_days: every date final settlement left something for - figures, NSS lines or closing
--   cash lines (UNION also removes duplicates) - so a day that has only some of them still appears,
--   and fails. The open day is not a closed day: its payments and Fed lines wait for its final
--   settlement.
-- account_mismatches: the FULL JOIN keeps a figure without a closing line AND a closing line without
--   a figure. IS DISTINCT FROM is "<>" that treats NULL as a value: NULL <> 5 is NULL (unknown), which
--   WHERE would drop, but NULL IS DISTINCT FROM 5 is TRUE - a missing row counts as a mismatch.
-- banks: every (day, bank) found in any of the three bank lists; bank_mismatches LEFT JOINs each
--   list to it. A row with a NULL bank is counted explicitly: "=" never matches a NULL, so its
--   LEFT JOINs find nothing on either side, and NULL IS DISTINCT FROM NULL is FALSE.
-- totals: everything side by side; a day with nothing of one kind counts 0 (COALESCE).
WITH figures AS (
  SELECT business_date, SUM(final_net_cents)::bigint AS figures_total_cents
  FROM settlement_figures
  GROUP BY business_date
),
nss AS (
  SELECT business_date, SUM(net_net_cents)::bigint AS nss_total_cents
  FROM nss_file_lines
  GROUP BY business_date
),
payments AS (
  SELECT c.business_date, SUM(c.amount_cents)::bigint AS payments_total_cents
  FROM cash_ledger_entries c
  JOIN operations o ON o.id = c.operation_id
  WHERE o.operation_type = 'SETTLEMENT_PROGRESS_PAYMENT'
    AND c.ledger = 'PARTICIPANT_SETTLEMENT'
  GROUP BY c.business_date
),
fed AS (
  SELECT business_date, SUM(amount_cents)::bigint AS fed_total_cents
  FROM cash_ledger_entries
  WHERE ledger = 'DTC_FED_ACCOUNT'
  GROUP BY business_date
),
closing_lines AS (
  SELECT c.business_date, c.account_id, SUM(c.amount_cents)::bigint AS closing_cents
  FROM cash_ledger_entries c
  JOIN operations o ON o.id = c.operation_id
  WHERE o.operation_type = 'FINAL_SETTLEMENT'
    AND c.ledger = 'PARTICIPANT_SETTLEMENT'
  GROUP BY c.business_date, c.account_id
),
bank_figures AS (
  SELECT f.business_date, a.settling_bank_id, SUM(f.final_net_cents)::bigint AS expected_cents
  FROM settlement_figures f
  JOIN accounts a ON a.id = f.account_id
  GROUP BY f.business_date, a.settling_bank_id
),
bank_fed AS (
  SELECT c.business_date, c.settling_bank_id, SUM(c.amount_cents)::bigint AS fed_cents
  FROM cash_ledger_entries c
  JOIN operations o ON o.id = c.operation_id
  WHERE o.operation_type = 'FINAL_SETTLEMENT'
    AND c.ledger = 'DTC_FED_ACCOUNT'
  GROUP BY c.business_date, c.settling_bank_id
),
closed_days AS (
  SELECT business_date FROM figures
  UNION
  SELECT business_date FROM nss
  UNION
  SELECT business_date FROM closing_lines
  UNION
  SELECT business_date FROM bank_fed
),
account_mismatches AS (
  SELECT COALESCE(f.business_date, l.business_date) AS business_date,
         COUNT(*)::integer                           AS accounts_mismatched
  FROM settlement_figures f
  FULL JOIN closing_lines l
         ON l.business_date = f.business_date
        AND l.account_id    = f.account_id
  WHERE COALESCE(l.closing_cents, 0) IS DISTINCT FROM -f.final_net_cents
  GROUP BY COALESCE(f.business_date, l.business_date)
),
banks AS (
  SELECT business_date, settling_bank_id FROM bank_figures
  UNION
  SELECT business_date, settling_bank_id FROM nss_file_lines
  UNION
  SELECT business_date, settling_bank_id FROM bank_fed
),
bank_mismatches AS (
  SELECT b.business_date, COUNT(*)::integer AS banks_mismatched
  FROM banks b
  LEFT JOIN bank_figures e
         ON e.business_date = b.business_date AND e.settling_bank_id = b.settling_bank_id
  LEFT JOIN nss_file_lines n
         ON n.business_date = b.business_date AND n.settling_bank_id = b.settling_bank_id
  LEFT JOIN bank_fed fd
         ON fd.business_date = b.business_date AND fd.settling_bank_id = b.settling_bank_id
  WHERE b.settling_bank_id IS NULL
     OR n.net_net_cents IS DISTINCT FROM e.expected_cents
     OR COALESCE(fd.fed_cents, 0) IS DISTINCT FROM n.net_net_cents
  GROUP BY b.business_date
),
totals AS (
  SELECT d.business_date,
         COALESCE(f.figures_total_cents, 0)  AS figures_total_cents,
         COALESCE(n.nss_total_cents, 0)      AS nss_total_cents,
         COALESCE(p.payments_total_cents, 0) AS payments_total_cents,
         COALESCE(fd.fed_total_cents, 0)     AS fed_total_cents,
         COALESCE(am.accounts_mismatched, 0) AS accounts_mismatched,
         COALESCE(bm.banks_mismatched, 0)    AS banks_mismatched
  FROM closed_days d
  LEFT JOIN figures f             ON f.business_date  = d.business_date
  LEFT JOIN nss n                 ON n.business_date  = d.business_date
  LEFT JOIN payments p            ON p.business_date  = d.business_date
  LEFT JOIN fed fd                ON fd.business_date = d.business_date
  LEFT JOIN account_mismatches am ON am.business_date = d.business_date
  LEFT JOIN bank_mismatches bm    ON bm.business_date = d.business_date
)
SELECT business_date,
       figures_total_cents,
       nss_total_cents,
       payments_total_cents,
       fed_total_cents,
       accounts_mismatched,
       banks_mismatched,
       figures_total_cents = nss_total_cents
         AND nss_total_cents = payments_total_cents
         AND fed_total_cents = 0
         AND accounts_mismatched = 0
         AND banks_mismatched = 0 AS ok
FROM totals
ORDER BY business_date;

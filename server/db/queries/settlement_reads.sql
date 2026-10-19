-- settlement_reads.sql   (Lesson 14 - the read side of DVP settlement)
--   GET /api/settlement/day          settlement_day_summary
--   GET /api/settlement/accounts     settlement_account_for / settlement_accounts_all
--   GET /api/delivery-orders         delivery_instructions_for_account / delivery_instructions_all
--   GET /api/settlement/closed-days  closed_day_figures + closed_day_nss_lines
-- Plain reads: no transaction block needed, and nothing here locks a row (no FOR UPDATE), so a
-- screen refresh never waits for the settlement engine.
--
-- BEFORE any SQL the APP has already:
--   * loaded the session user (none -> 401); closed days: role must be AUDITOR or OPERATOR (else 403)
--   * looked up asset_id once with holdings.sql find_asset_by_symbol ('DEMO-TREASURY')
--   * chosen the statement from the SESSION user, never from the request (query parameters are ignored):
--       PARTICIPANT          -> the *_for / *_for_account statement, account_id = their own account
--       AUDITOR or OPERATOR  -> the *_all statement
--
-- PARAMETERS ARE NAMED: {asset_id} or {account_id, asset_id}; the delivery list for a participant
--   takes {account_id}; delivery_instructions_all and the two closed-day statements take none.
-- Money columns are BIGINT cents: psycopg returns Python ints and FastAPI sends JSON numbers.
--   No SUM() appears in this file, so there is no NUMERIC (Decimal) to cast back.
-- Dates: psycopg returns business_date as a Python date and created_at as a timezone-aware
--   datetime; FastAPI sends both as ISO-8601 strings ("2026-10-19").

-- name: settlement_day_summary
-- asset_id = the demo instrument
-- One row: the day the engine is on, its phase, DTC's maximum net debit cap (scaled down, see seed 004)
-- and what one unit of the instrument is worth as collateral.
-- settlement_day has exactly ONE row (singleton primary key), and collateral_prices one row per
-- asset, so the CROSS JOIN pairs the one day row with the one price row: 1 x 1 = 1 row.
SELECT d.business_date,
       d.phase,                         -- 'OPEN' or 'VALUED_CUTOFF_PASSED'
       d.max_net_debit_cap_cents,
       c.prior_close_cents,             -- yesterday's closing price
       c.haircut_bps,                   -- 1 bp = 0.01 per cent
       c.collateral_cents_per_unit      -- generated column: prior close less the haircut
FROM settlement_day d
CROSS JOIN collateral_prices c
WHERE c.asset_id = %(asset_id)s;
-- APP DECIDES: 0 rows -> 500 "run the seeds first" (seed 004 creates both rows).
--   1 row -> sent as it is.

-- name: settlement_account_for
-- account_id = account_id of the logged-in participant; asset_id = the demo instrument
-- One account's settlement balance with the two risk controls worked out:
--   collateral_monitor_cents = collateral value + net settlement balance   (DTC: must not go negative)
--   headroom_cents           = the largest NEW debit the account could take right now without breaking
--                              either control: it may fall by at most CM (to reach CM = 0) and by at
--                              most cap + net (to reach net = -cap), so the smaller of the two;
--                              GREATEST(0, ...) shows 0 instead of a negative number.
-- LEFT JOIN settling_banks: accounts.settling_bank_id may be NULL (an account with no bank still shows).
-- LEFT JOIN net_debit_cap_inputs: an account without cap inputs still shows (with NULL inputs);
--   reconciliation check_risk_controls is what flags it.
-- JOIN positions: seeds 003 and 004 create one position and one settlement row per account.
SELECT sa.account_id,
       a.participant_name,
       a.settling_bank_id,
       b.bank_name,
       p.traditional_units,
       p.tokenized_units,
       sa.net_cents,                    -- + credit (DTC owes it), - debit (it owes DTC)
       sa.collateral_value_cents,       -- traditional units x collateral per unit; tokenized count 0
       sa.collateral_value_cents + sa.net_cents AS collateral_monitor_cents,
       sa.net_debit_cap_cents,
       GREATEST(0, LEAST(sa.collateral_value_cents + sa.net_cents,
                         sa.net_debit_cap_cents + sa.net_cents)) AS headroom_cents,
       n.avg_top3_peak_cents,           -- the cap's inputs, so the screen can show how it was computed
       n.factor_bps
FROM settlement_accounts sa
JOIN accounts a                  ON a.id = sa.account_id
LEFT JOIN settling_banks b       ON b.id = a.settling_bank_id
JOIN positions p                 ON p.account_id = sa.account_id
                                AND p.asset_id = %(asset_id)s
LEFT JOIN net_debit_cap_inputs n ON n.account_id = sa.account_id
WHERE sa.account_id = %(account_id)s
ORDER BY sa.account_id;
-- APP DECIDES: sends the list as it is (one row; an empty list if the seed has not run).
-- Demo, fresh seed, alpha: net 0, collateral 9,500,000, CM 9,500,000, cap 3,844,000,
--   headroom LEAST(9,500,000, 3,844,000) = 3,844,000.

-- name: settlement_accounts_all
-- AUDITOR or OPERATOR: every account. Same columns and joins as settlement_account_for, no WHERE.
SELECT sa.account_id,
       a.participant_name,
       a.settling_bank_id,
       b.bank_name,
       p.traditional_units,
       p.tokenized_units,
       sa.net_cents,
       sa.collateral_value_cents,
       sa.collateral_value_cents + sa.net_cents AS collateral_monitor_cents,
       sa.net_debit_cap_cents,
       GREATEST(0, LEAST(sa.collateral_value_cents + sa.net_cents,
                         sa.net_debit_cap_cents + sa.net_cents)) AS headroom_cents,
       n.avg_top3_peak_cents,
       n.factor_bps
FROM settlement_accounts sa
JOIN accounts a                  ON a.id = sa.account_id
LEFT JOIN settling_banks b       ON b.id = a.settling_bank_id
JOIN positions p                 ON p.account_id = sa.account_id
                                AND p.asset_id = %(asset_id)s
LEFT JOIN net_debit_cap_inputs n ON n.account_id = sa.account_id
ORDER BY sa.account_id;
-- APP DECIDES: sends the rows as they are, lowest account id first.

-- name: delivery_instructions_for_account
-- account_id = account_id of the logged-in participant: orders it DELIVERS or RECEIVES.
-- The same named parameter appears twice; psycopg sends the value once.
-- Each order is joined to the operations it points at:
--   sub = its DELIVER_ORDER row (the request): who submitted it, and when
--   dvp = its DVP_SETTLEMENT row (the event that moved units and money), only once it SETTLED
-- JOIN operations sub: submitted_operation_id is NOT NULL and a foreign key, so every order has one.
-- JOIN users: a DELIVER_ORDER always has an actor (operations_columns_match_type), so no row is lost.
-- LEFT JOIN operations dvp: settlement_operation_id is NULL while the order is RECYCLING or after
--   it was DROPPED_AT_CUTOFF - a plain JOIN would hide every order that has not settled.
SELECT di.id,
       di.business_date,
       di.deliverer_account_id,
       di.receiver_account_id,
       di.quantity,
       di.amount_cents,
       di.status,                       -- SETTLED / RECYCLING / DROPPED_AT_CUTOFF
       di.recycle_reason,               -- NULL once SETTLED
       di.submitted_operation_id,
       di.settlement_operation_id,
       u.username     AS submitted_by,
       sub.created_at AS submitted_at,
       dvp.created_at AS settled_at     -- NULL until it settles
FROM delivery_instructions di
JOIN operations sub      ON sub.id = di.submitted_operation_id
JOIN users u             ON u.id = sub.actor_user_id
LEFT JOIN operations dvp ON dvp.id = di.settlement_operation_id
WHERE di.deliverer_account_id = %(account_id)s
   OR di.receiver_account_id = %(account_id)s
ORDER BY di.id DESC
LIMIT 100;
-- APP DECIDES: each row's recycle_reason goes through risk.reason_for_viewer(viewer = own account):
--   a reason about the OTHER side (e.g. the receiver's net debit cap, seen by the deliverer) is
--   shown as WAITING_ON_COUNTERPARTY - a lab choice: a participant never learns another's limits.
-- settled_at is a transaction START time (like every created_at): an order that recycled settles
--   inside a LATER request (a credit to someone), so its settled_at is later than submitted_at.

-- name: delivery_instructions_all
-- AUDITOR or OPERATOR: every order, newest first. Same columns and joins, no WHERE.
SELECT di.id,
       di.business_date,
       di.deliverer_account_id,
       di.receiver_account_id,
       di.quantity,
       di.amount_cents,
       di.status,
       di.recycle_reason,
       di.submitted_operation_id,
       di.settlement_operation_id,
       u.username     AS submitted_by,
       sub.created_at AS submitted_at,
       dvp.created_at AS settled_at
FROM delivery_instructions di
JOIN operations sub      ON sub.id = di.submitted_operation_id
JOIN users u             ON u.id = sub.actor_user_id
LEFT JOIN operations dvp ON dvp.id = di.settlement_operation_id
ORDER BY di.id DESC
LIMIT 100;
-- APP DECIDES: sends the rows as they are - the exact recycle reasons (the overseers see both sides).

-- name: closed_day_figures
-- AUDITOR or OPERATOR (no parameters): each account's final figure for the 10 most recent CLOSED days.
-- A day is closed once final settlement has written its figures, so the closed days are the
-- distinct business dates in settlement_figures. recent_days is a CTE (WITH ... AS): a named
-- sub-result used like a table below. DISTINCT first, then ORDER BY ... DESC LIMIT 10 keeps the
-- newest 10 dates; the JOIN keeps only the figures of those dates.
WITH recent_days AS (
  SELECT DISTINCT business_date
  FROM settlement_figures
  ORDER BY business_date DESC
  LIMIT 10
)
SELECT f.business_date,
       f.account_id,
       a.participant_name,
       f.final_net_cents                -- the net just before final settlement set it to 0
FROM settlement_figures f
JOIN recent_days r ON r.business_date = f.business_date
JOIN accounts a    ON a.id = f.account_id
ORDER BY f.business_date DESC, f.account_id;
-- APP DECIDES: groups the rows by business_date, newest day first (the order they arrive in).
--   Demo day: alpha +3,000,000, beta -1,000,000, gamma -1,950,000, Delta 0.

-- name: closed_day_nss_lines
-- AUDITOR or OPERATOR (no parameters): the simulated NSS file of the same 10 days - one net-net
-- figure per settling bank (+ the bank is paid, - the bank pays).
-- The SAME recent_days CTE (dates from settlement_figures), so both lists describe the same days.
WITH recent_days AS (
  SELECT DISTINCT business_date
  FROM settlement_figures
  ORDER BY business_date DESC
  LIMIT 10
)
SELECT l.business_date,
       l.settling_bank_id,
       b.bank_name,
       l.net_net_cents
FROM nss_file_lines l
JOIN recent_days r    ON r.business_date = l.business_date
JOIN settling_banks b ON b.id = l.settling_bank_id
ORDER BY l.business_date DESC, l.settling_bank_id;
-- APP DECIDES: attaches each line to its day from closed_day_figures, which runs FIRST. The two
--   reads are separate statements (READ COMMITTED: each sees what was committed when IT started),
--   so a day that closes in between has NSS lines but no figures here: the app leaves it out, and
--   the next refresh shows it whole.
--   Demo day: bank 201 +2,000,000, bank 202 -1,950,000.

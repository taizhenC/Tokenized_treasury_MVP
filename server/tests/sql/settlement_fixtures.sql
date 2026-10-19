-- settlement_fixtures.sql   (Lesson 14 - deliberate corruptions for the settlement reconciliation drills)
-- Each corruption breaks ONE thing behind the app's back - the way a bug, a hand-made "fix" or a
-- bad migration could - and its undo puts it back. test_85 runs them on its own connection:
--   report passes -> corrupt -> the report must fail exactly the check that guards it -> undo -> passes.
-- The older drills (A-E, units only) are in tests/sql/fixtures.sql.
--
-- BEFORE any SQL here the TEST has already:
--   * made sure gamma holds at least 100 traditional units (drill S12 debits gamma)
--   * closed one settlement day through the API: alpha's deliver order to beta (2 units for
--     $190.00) SETTLED, beta made a settlement progress payment of $10.00 and gamma one of $5.00,
--     then the operator ran the valued cutoff and final settlement - so there is a settled order,
--     a closed day with figures and an NSS file, and every net is back to 0 on the new open day.
--     That day's figures: alpha +19,000, beta -18,000, gamma +500, Delta 0 cents; its NSS file:
--     Bank One (alpha + beta) +1,000, Bank Two (gamma + Delta) +500 - both banks have a closing
--     Fed line (drill S13 shifts money between them)
--   * given alpha some tokenized units (drill S4 needs them)
--   * checked that the reconciliation report passes
--
-- PARAMETERS ARE NAMED:
--   {instruction_id}    that settled order (alpha 101 -> beta 102)
--   {wrong_account_id}  an account that took no part in it: gamma, 103
--   Blocks without a placeholder ignore the dict and are sent exactly as written.
-- AUTOCOMMIT: each statement commits on its own. A drill that needs several statements (S5) runs
--   them one after the other; between them the books are briefly inconsistent, which is fine:
--   nothing else runs while test_85 drills.
--   The exception is the last section (missing rows): those blocks run inside ONE transaction that
--   the test always rolls back, together with the checks, so they need no undo.
-- These are UPDATEs and INSERTs straight on the tables, so no app check runs. Only the CHECK
--   constraints still apply - and every corruption below keeps every CHECK true. That is the point:
--   the database cannot see these mistakes row by row; only reconciliation, comparing whole
--   records with each other, can.
-- In this file the "app" is the test suite: each "-- APP DECIDES:" note says which check must flag
--   the corruption. Every corruption and every undo must change at least one row.


-- ---------- check_cash_lines_net_to_zero ----------

-- name: corrupt_s1_unbalanced_cash_line
-- S1: a stray +1 cent line on DTC's Fed account (account_id NULL), dated today, attached to the
--     first OPENING operation. That operation's cash lines now add up to +1 cent instead of 0.
--     No settlement balance names it, and today is not a closed day, so no other check sees it.
-- The two scalar subqueries pick the operation and the open day; the VALUES row uses them.
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES ((SELECT min(id) FROM operations WHERE operation_type = 'OPENING'),
        (SELECT business_date FROM settlement_day),
        'DTC_FED_ACCOUNT', NULL, 1);
-- APP DECIDES: cashLinesNetToZero fails with failing = 1; every other check still passes.

-- name: undo_s1
-- No real cash line ever belongs to an OPENING operation, so this deletes exactly the stray one.
DELETE FROM cash_ledger_entries
WHERE operation_id = (SELECT min(id) FROM operations WHERE operation_type = 'OPENING');


-- ---------- check_settlement_vs_cash_ledger ----------

-- name: corrupt_s2_net_without_cash_line
-- S2: gamma's settlement balance is "fixed" by hand: +100 cents, with no cash line behind it.
--     Both risk CHECKs still hold (a credit only makes them safer).
UPDATE settlement_accounts
SET net_cents = net_cents + 100
WHERE account_id = 103;
-- APP DECIDES: settlementVsCashLedger flags account 103 only (stored 100 cents more than its lines).

-- name: undo_s2
UPDATE settlement_accounts
SET net_cents = net_cents - 100
WHERE account_id = 103;


-- ---------- check_collateral_vs_positions ----------

-- name: corrupt_s3_collateral_without_units
-- S3: beta's collateral value grows by one unit's worth ($95.00) although no unit arrived - like a
--     delivery that updated the collateral but forgot the position.
UPDATE settlement_accounts
SET collateral_value_cents = collateral_value_cents + 9500
WHERE account_id = 102;
-- APP DECIDES: collateralVsPositions flags account 102 only.

-- name: undo_s3
UPDATE settlement_accounts
SET collateral_value_cents = collateral_value_cents - 9500
WHERE account_id = 102;

-- name: corrupt_s4_tokenized_counted_as_collateral
-- S4: alpha's TOKENIZED units are counted as collateral too. Under the SEC staff no-action letter
--     of 11 Dec 2025 DTC gives tokenized entitlements no collateral value, so this is the one
--     mistake the lab's rule exists to prevent.
-- UPDATE ... FROM (Postgres): the FROM list brings in other tables, and the WHERE joins them to the
--   row being updated - here alpha's position (how many tokenized units) and the asset's price.
UPDATE settlement_accounts s
SET collateral_value_cents = s.collateral_value_cents + p.tokenized_units * c.collateral_cents_per_unit
FROM positions p
JOIN collateral_prices c ON c.asset_id = p.asset_id
WHERE p.account_id = s.account_id
  AND p.asset_id = 1
  AND s.account_id = 101;
-- APP DECIDES: collateralVsPositions flags account 101 only: stored - expected = tokenized x 9,500.

-- name: undo_s4
-- The same arithmetic with the sign swapped (alpha's tokenized units have not changed since).
UPDATE settlement_accounts s
SET collateral_value_cents = s.collateral_value_cents - p.tokenized_units * c.collateral_cents_per_unit
FROM positions p
JOIN collateral_prices c ON c.asset_id = p.asset_id
WHERE p.account_id = s.account_id
  AND p.asset_id = 1
  AND s.account_id = 101;


-- ---------- check_settled_instructions_vs_ledgers ----------

-- name: corrupt_s5_ledger_line
-- S5: the settled order's units went to the WRONG account, and the books say so CONSISTENTLY:
--     the receiver's +q ledger line, its position and its collateral all move to wrong_account_id.
--     Conservation, positions vs. ledger and collateral vs. positions all still pass: this is the
--     "wrong recipient" that the Lesson 9 report could not see. Only the instruction disagrees.
-- Step 1 of 3: the DVP_SETTLEMENT's +q TRADITIONAL line (the receiver's) now names the wrong account.
-- UPDATE ... FROM delivery_instructions: the order tells us which operation and which receiver.
UPDATE ledger_entries l
SET account_id = %(wrong_account_id)s
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND l.operation_id = d.settlement_operation_id
  AND l.account_id = d.receiver_account_id
  AND l.units_delta > 0;
-- APP DECIDES: 1 row changed.

-- name: corrupt_s5_positions
-- Step 2 of 3: the units themselves follow the ledger line: receiver -q, wrong account +q.
-- CASE picks the sign per row; the IN list limits the UPDATE to exactly those two position rows.
UPDATE positions p
SET traditional_units = p.traditional_units
                        + CASE WHEN p.account_id = d.receiver_account_id THEN -d.quantity
                               ELSE d.quantity
                          END
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND p.asset_id = d.asset_id
  AND p.account_id IN (d.receiver_account_id, %(wrong_account_id)s);
-- APP DECIDES: 2 rows changed.

-- name: corrupt_s5_collateral
-- Step 3 of 3: the collateral follows the units (q x 9,500 cents each way), so the collateral check
-- passes too. FROM delivery_instructions JOIN collateral_prices: the order's quantity and its price.
UPDATE settlement_accounts s
SET collateral_value_cents = s.collateral_value_cents
                             + CASE WHEN s.account_id = d.receiver_account_id THEN -1 ELSE 1 END
                               * d.quantity * c.collateral_cents_per_unit
FROM delivery_instructions d
JOIN collateral_prices c ON c.asset_id = d.asset_id
WHERE d.id = %(instruction_id)s
  AND s.account_id IN (d.receiver_account_id, %(wrong_account_id)s);
-- APP DECIDES: 2 rows changed. Now settledInstructionsVsLedgers fails (failing = 1, no orphan)
--   and every other check passes.

-- name: undo_s5_collateral
UPDATE settlement_accounts s
SET collateral_value_cents = s.collateral_value_cents
                             - CASE WHEN s.account_id = d.receiver_account_id THEN -1 ELSE 1 END
                               * d.quantity * c.collateral_cents_per_unit
FROM delivery_instructions d
JOIN collateral_prices c ON c.asset_id = d.asset_id
WHERE d.id = %(instruction_id)s
  AND s.account_id IN (d.receiver_account_id, %(wrong_account_id)s);

-- name: undo_s5_positions
UPDATE positions p
SET traditional_units = p.traditional_units
                        - CASE WHEN p.account_id = d.receiver_account_id THEN -d.quantity
                               ELSE d.quantity
                          END
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND p.asset_id = d.asset_id
  AND p.account_id IN (d.receiver_account_id, %(wrong_account_id)s);

-- name: undo_s5_ledger_line
UPDATE ledger_entries l
SET account_id = d.receiver_account_id
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND l.operation_id = d.settlement_operation_id
  AND l.account_id = %(wrong_account_id)s
  AND l.units_delta > 0;

-- name: corrupt_s6_amount_changed_after_settlement
-- S6: the order's price is changed after it settled: the instruction now says 1 cent more than the
--     money that actually moved. The independent record of the instruction disagrees with the books.
UPDATE delivery_instructions
SET amount_cents = amount_cents + 1
WHERE id = %(instruction_id)s;
-- APP DECIDES: settledInstructionsVsLedgers fails (failing = 1, no orphan).

-- name: undo_s6
UPDATE delivery_instructions
SET amount_cents = amount_cents - 1
WHERE id = %(instruction_id)s;

-- name: corrupt_s11_operation_names_the_wrong_receiver
-- S11: the settlement EVENT names the wrong receiver, while every ledger line is still right.
--     S6 changes the amount, which the operation AND the cash lines are both compared with, so it
--     cannot show that each comparison works on its own; this drill breaks ONLY the operation match.
--     The operation still names two different accounts (operations_no_self_transfer holds), and no
--     other check reads a DVP operation's account columns.
UPDATE operations o
SET destination_account_id = %(wrong_account_id)s
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND o.id = d.settlement_operation_id;
-- APP DECIDES: 1 row changed. settledInstructionsVsLedgers fails (failing = 1, no orphan) and
--   every other check passes.

-- name: undo_s11
UPDATE operations o
SET destination_account_id = d.receiver_account_id
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND o.id = d.settlement_operation_id;

-- name: corrupt_s12_cash_line
-- S12: the settled order's MONEY went to the wrong account, and the books say so CONSISTENTLY - the
--     money version of S5. Step 1 of 2: the receiver's -amount cash line now names wrong_account_id.
--     The operation's cash lines still add up to 0 (check 3), and the operation itself is untouched.
UPDATE cash_ledger_entries c
SET account_id = %(wrong_account_id)s
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND c.operation_id = d.settlement_operation_id
  AND c.account_id = d.receiver_account_id
  AND c.amount_cents < 0;
-- APP DECIDES: 1 row changed.

-- name: corrupt_s12_nets
-- Step 2 of 2: the stored nets follow the cash line, so each balance still equals its cash history
-- (check 4): the receiver lost a -amount line (net + amount), the wrong account gained it (net -
-- amount). Gamma holds at least 100 units ($9,500 of collateral), so its -$190.00 still passes both
-- risk CHECKs and check 7. The closed day's figures and closing lines are not touched (check 8).
UPDATE settlement_accounts s
SET net_cents = s.net_cents
                + CASE WHEN s.account_id = d.receiver_account_id THEN d.amount_cents
                       ELSE -d.amount_cents
                  END
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND s.account_id IN (d.receiver_account_id, %(wrong_account_id)s);
-- APP DECIDES: 2 rows changed. Now settledInstructionsVsLedgers fails (failing = 1, no orphan)
--   and every other check passes.

-- name: undo_s12_nets
UPDATE settlement_accounts s
SET net_cents = s.net_cents
                - CASE WHEN s.account_id = d.receiver_account_id THEN d.amount_cents
                       ELSE -d.amount_cents
                  END
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND s.account_id IN (d.receiver_account_id, %(wrong_account_id)s);

-- name: undo_s12_cash_line
UPDATE cash_ledger_entries c
SET account_id = d.receiver_account_id
FROM delivery_instructions d
WHERE d.id = %(instruction_id)s
  AND c.operation_id = d.settlement_operation_id
  AND c.account_id = %(wrong_account_id)s
  AND c.amount_cents < 0;

-- name: corrupt_s7_orphan_settlement
-- S7: the order is unlinked from its settlement event, as if it had been dropped at the cutoff:
--     units and money moved under a DVP_SETTLEMENT that no instruction asked for.
-- All three columns change together, so the CHECKs delivery_settled_has_operation and
-- delivery_reason_matches_status still hold.
UPDATE delivery_instructions
SET status                  = 'DROPPED_AT_CUTOFF',
    recycle_reason          = 'RECEIVER_NET_DEBIT_CAP',
    settlement_operation_id = NULL
WHERE id = %(instruction_id)s;
-- APP DECIDES: settledInstructionsVsLedgers fails with orphan_settlements = 1 (and failing = 0:
--   the order is no longer SETTLED, so it is no longer one of the orders checked).

-- name: undo_s7
-- The settlement event is found again by its SYSTEM key: 'dvp-' || the id of the DELIVER_ORDER
-- (the correlated subquery reads d.submitted_operation_id from the row being updated).
-- If it were not found, the subquery would give NULL and delivery_settled_has_operation would fail.
UPDATE delivery_instructions d
SET status                  = 'SETTLED',
    recycle_reason          = NULL,
    settlement_operation_id = (SELECT o.id
                               FROM operations o
                               WHERE o.operation_type = 'DVP_SETTLEMENT'
                                 AND o.actor_user_id IS NULL
                                 AND o.idempotency_key = 'dvp-' || d.submitted_operation_id)
WHERE d.id = %(instruction_id)s;


-- ---------- check_risk_controls ----------

-- name: corrupt_s8_cap_typed_by_hand
-- S8: beta's net debit cap is raised by hand ($1,000.00) instead of being computed from its inputs
--     (average of the 3 highest peaks x factor). Net and CM still satisfy the CHECKs.
UPDATE settlement_accounts
SET net_debit_cap_cents = net_debit_cap_cents + 100000
WHERE account_id = 102;
-- APP DECIDES: riskControls flags account 102 only (stored cap - expected cap = 100,000).

-- name: undo_s8
UPDATE settlement_accounts
SET net_debit_cap_cents = net_debit_cap_cents - 100000
WHERE account_id = 102;

-- name: corrupt_s9_maximum_cap_lowered
-- S9: DTC's maximum net debit cap drops by 1 cent, but no stored cap is recomputed. Only alpha's cap
--     is AT the maximum (its own formula gives more), so only alpha's stored cap is now stale.
UPDATE settlement_day
SET max_net_debit_cap_cents = max_net_debit_cap_cents - 1;
-- APP DECIDES: riskControls flags account 101 only (expected cap = the new maximum, 1 cent less).

-- name: undo_s9
UPDATE settlement_day
SET max_net_debit_cap_cents = max_net_debit_cap_cents + 1;


-- ---------- check_closed_days ----------

-- name: corrupt_s10_closed_day_figure_changed
-- S10: alpha's final figure for the most recent closed day is changed after the day closed. The
--      NSS file (and the money the settling banks paid) still say the old amount.
UPDATE settlement_figures
SET final_net_cents = final_net_cents + 1
WHERE account_id = 101
  AND business_date = (SELECT max(business_date) FROM settlement_figures);
-- APP DECIDES: closedDays flags that one day: the figures total 1 cent more than the NSS total,
--   alpha's figure is no longer minus its closing line, and Bank One's NSS line is no longer the
--   sum of its accounts' figures.

-- name: undo_s10
UPDATE settlement_figures
SET final_net_cents = final_net_cents - 1
WHERE account_id = 101
  AND business_date = (SELECT max(business_date) FROM settlement_figures);

-- name: corrupt_s13_nss_lines_shifted_between_banks
-- S13: $1.00 of the most recent NSS file is moved from Bank Two to Bank One (+100 on 201, -100 on
--      202), and step 2 moves the money at the Fed the same way. Every TOTAL still agrees (figures =
--      NSS = payments, the Fed lines still net to 0) - only a bank-by-bank comparison with the figures
--      can see that Bank One is now paid $1.00 that belongs to Bank Two.
-- Step 1 of 2. CASE picks the sign per bank.
UPDATE nss_file_lines
SET net_net_cents = net_net_cents + CASE WHEN settling_bank_id = 201 THEN 100 ELSE -100 END
WHERE business_date = (SELECT max(business_date) FROM nss_file_lines);
-- APP DECIDES: 2 rows changed.

-- name: corrupt_s13_fed_lines_shifted_between_banks
-- Step 2 of 2: the two banks' closing lines on DTC's Fed account follow the NSS file. Final
-- settlement's own cash lines still add up to 0 (+100 - 100), so check 3 passes.
-- UPDATE ... FROM operations: only the FINAL_SETTLEMENT's Fed lines (a payment's Fed line names no bank).
UPDATE cash_ledger_entries c
SET amount_cents = c.amount_cents + CASE WHEN c.settling_bank_id = 201 THEN 100 ELSE -100 END
FROM operations o
WHERE o.id = c.operation_id
  AND o.operation_type = 'FINAL_SETTLEMENT'
  AND c.ledger = 'DTC_FED_ACCOUNT'
  AND c.business_date = (SELECT max(business_date) FROM nss_file_lines);
-- APP DECIDES: 2 rows changed. closedDays flags that one day with banks_mismatched = 2 (and
--   accounts_mismatched = 0, every total still equal); every other check passes.

-- name: undo_s13_fed_lines
UPDATE cash_ledger_entries c
SET amount_cents = c.amount_cents - CASE WHEN c.settling_bank_id = 201 THEN 100 ELSE -100 END
FROM operations o
WHERE o.id = c.operation_id
  AND o.operation_type = 'FINAL_SETTLEMENT'
  AND c.ledger = 'DTC_FED_ACCOUNT'
  AND c.business_date = (SELECT max(business_date) FROM nss_file_lines);

-- name: undo_s13_nss_lines
UPDATE nss_file_lines
SET net_net_cents = net_net_cents - CASE WHEN settling_bank_id = 201 THEN 100 ELSE -100 END
WHERE business_date = (SELECT max(business_date) FROM nss_file_lines);

-- name: corrupt_s14_nss_line_raised
-- S14: the closed day's figures and NSS file both say 1 cent more than DTC was paid in that day.
--      Step 1 of 2: Bank One's NSS line +1 cent.
UPDATE nss_file_lines
SET net_net_cents = net_net_cents + 1
WHERE settling_bank_id = 201
  AND business_date = (SELECT max(business_date) FROM nss_file_lines);
-- APP DECIDES: 1 row changed.

-- name: corrupt_s14_figure_raised
-- Step 2 of 2: alpha's figure +1 cent (alpha settles through Bank One), so figures = NSS again and
-- Bank One's line is still the sum of its figures. What no longer agrees: the NSS total and that
-- day's payments - and, line by line, alpha's figure with its closing line and Bank One's NSS line
-- with its Fed line. (Those two comparisons also see it; the totals say what it means.)
UPDATE settlement_figures
SET final_net_cents = final_net_cents + 1
WHERE account_id = 101
  AND business_date = (SELECT max(business_date) FROM settlement_figures);
-- APP DECIDES: 1 row changed. closedDays flags that one day: nss_total - payments_total = 1 and
--   figures_total = nss_total; every other check passes.

-- name: undo_s14_figure
UPDATE settlement_figures
SET final_net_cents = final_net_cents - 1
WHERE account_id = 101
  AND business_date = (SELECT max(business_date) FROM settlement_figures);

-- name: undo_s14_nss_line
UPDATE nss_file_lines
SET net_net_cents = net_net_cents - 1
WHERE settling_bank_id = 201
  AND business_date = (SELECT max(business_date) FROM nss_file_lines);

-- name: corrupt_s15_payment_fed_line_moved_to_the_open_day
-- S15: DTC's Fed account no longer ends the closed day at 0: the Fed line of that day's first
--      settlement progress payment (beta's, -$10.00) is re-dated to the open day. The payment's two
--      lines still add up to 0 (check 3 groups by operation), its participant line - the money
--      paid in - stays on the closed day, and the open day is not a closed day, so it is not checked.
-- The scalar subquery picks the line: the lowest id among that day's payment Fed lines.
UPDATE cash_ledger_entries
SET business_date = (SELECT business_date FROM settlement_day)
WHERE id = (SELECT min(c.id)
            FROM cash_ledger_entries c
            JOIN operations o ON o.id = c.operation_id
            WHERE o.operation_type = 'SETTLEMENT_PROGRESS_PAYMENT'
              AND c.ledger = 'DTC_FED_ACCOUNT'
              AND c.business_date = (SELECT max(business_date) FROM settlement_figures));
-- APP DECIDES: 1 row changed. closedDays flags that one day: fed_total_cents is no longer 0, while
--   figures = NSS = payments and nothing mismatches line by line; every other check passes.

-- name: undo_s15
-- A payment's two lines always share one date: put every Fed line back on the date of the
-- participant line of its own operation (the self-join pairs the two lines of one operation).
UPDATE cash_ledger_entries c
SET business_date = p.business_date
FROM cash_ledger_entries p
WHERE p.operation_id = c.operation_id
  AND p.ledger = 'PARTICIPANT_SETTLEMENT'
  AND c.ledger = 'DTC_FED_ACCOUNT'
  AND c.business_date <> p.business_date;


-- ---------- missing rows (each in ONE transaction that the test rolls back) ----------
-- A check that starts FROM the rows it checks passes when a row is MISSING: no row, no failing row.
-- These drills delete a row the app cannot work without and expect the report to FAIL.

-- name: missing_settlement_day
-- M1: no settlement_day row at all - every deliver order, payment, cutoff and final settlement would
--     answer 500, and DTC's maximum cap is gone.
DELETE FROM settlement_day;
-- APP DECIDES: 1 row deleted. riskControls fails for EVERY account (has_day is false); every other
--   check passes.

-- name: missing_figures_of_delta
-- M2, step 1 of 2: Delta's final figures (all $0) must go first: settlement_figures.account_id
-- references settlement_accounts. (Delta never had a cash line, so none blocks step 2.) A $0 figure
-- has no closing line and adds nothing to Bank Two's line, so check 8 is not affected.
DELETE FROM settlement_figures
WHERE account_id = 104;
-- APP DECIDES: one row per closed day deleted.

-- name: missing_settlement_account_of_delta
-- M2, step 2 of 2: Delta still has an account and a position (it can be named as a receiver), but no
-- settlement account: the engine would hold no state for it.
DELETE FROM settlement_accounts
WHERE account_id = 104;
-- APP DECIDES: 1 row deleted. settlementVsCashLedger, collateralVsPositions and riskControls each
--   flag account 104 (ok false, not NULL); every other check passes.

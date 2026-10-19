-- final_settlement.sql   (Lesson 13 - the settlement day, step 2 of 2)
-- POST /api/settlement-day/final-settlement  { "idempotencyKey": "..." }   (operator only)
-- Closes the day's MONEY: records each account's final net figure, writes the simulated NSS file
-- (one net-net figure per settling bank), posts the closing cash lines, sets every settlement
-- balance back to 0, and opens the next business day. Moves no units.
--
-- What DTC does at this step: securities settled one by one all day, but the money was not exchanged
-- delivery by delivery (only settlement progress payments came in during the day). Each
-- participant's settlement balance went up (it delivered) and down (it received) instead.
-- At about 3:45pm ET DTC produces the final figures; each participant's balance becomes ONE net
-- figure, and the participants that settle through the same settling bank are added up into ONE
-- "net-net" figure per settling bank. At about 4:30pm ET DTC sends those figures to the Federal
-- Reserve in the National Settlement Service (NSS) file, and the Fed moves the money between the
-- settling banks' accounts and DTC's account. Here the "NSS file" is a table (nss_file_lines),
-- not a Federal Reserve interface, and the operator triggers the step instead of a clock.
-- The two risk controls exist for this moment: securities left before the money, so every debit
-- must stay covered (CM) and limited (NDC). DTC's liquidity is designed so settlement completes
-- even if the largest participant or family fails to pay; this lab assumes every bank pays.
--
-- The demo day (README): final figures alpha +3,000,000, beta -1,000,000, gamma -1,950,000,
--   Delta 0 cents. NSS file: Bank One (alpha + beta) +2,000,000, Bank Two (gamma + Delta)
--   -1,950,000. The NSS lines add up to +50,000 = gamma's settlement progress payment: DVP
--   settlements only move money BETWEEN participants (they net to zero), so what is left over
--   is exactly the money already paid in during the day.
--
-- BEFORE any SQL the APP has already:
--   * loaded the session user; role must be OPERATOR (PARTICIPANT or AUDITOR -> 403 "operators only")
--   * validated the body with Pydantic (else 400): idempotencyKey 1..100 characters, no other
--     fields. The request has NO other input: which day closes comes from the database.
--   * computed request_hash = sha256 hex of the type only: 'FINAL_SETTLEMENT'
--
-- PARAMETERS ARE NAMED. One dict per request, passed to every statement that needs one:
--   {actor_user_id, idempotency_key, request_hash} + operation_id (after reserve)
--   + business_date (read from lock_settlement_day) + result (at the end).
--
-- ONE CONNECTION, ONE TRANSACTION:  with conn.transaction():  around every statement below.
--   The figures, the NSS file, the closing lines, the zeroed balances and the new day commit
--   TOGETHER or not at all: a crash halfway leaves the day exactly as it was, ready to run again.
-- ANY rejection, replay or conflict raises inside the block -> ROLLBACK. No operations row survives.
-- LOCK ORDER (the one global order: sequencer -> positions -> settlement accounts): this step
--   takes the SEQUENCER, then ALL settlement accounts. It changes no position, so it locks none.
-- RULE: after every UPDATE / INSERT check cursor.rowcount against what the app expects, otherwise
--   raise (-> ROLLBACK, 500).
-- ERROR MAPPING (after ROLLBACK): UniqueViolation 23505 (figures for this day already exist:
--   the PRIMARY KEYs allow one close per day) -> 409; OperationalError (connection lost,
--   pool timeout, lock_timeout) -> 503; anything else -> 500.

-- name: reserve_idempotency_key
-- Same shape as the valued cutoff: actor, key and hash only; accounts, asset, quantity and amount
-- stay NULL (the CHECK operations_columns_match_type requires that for FINAL_SETTLEMENT).
INSERT INTO operations
  (operation_type, actor_user_id, idempotency_key, request_hash)
VALUES
  ('FINAL_SETTLEMENT', %(actor_user_id)s, %(idempotency_key)s, %(request_hash)s)
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
--   same request_hash      -> replay: HTTP 200 + the stored result. The day does NOT close twice.
--   different request_hash -> HTTP 409 Conflict.

-- name: lock_settlement_day
-- The SETTLEMENT SEQUENCER (see valued_cutoff.sql). Holding it means no deliver order and no
-- settlement progress payment can change a settlement balance until this transaction ends.
SELECT business_date, phase, max_net_debit_cap_cents
FROM settlement_day
FOR UPDATE;
-- APP DECIDES:
--   0 rows -> 500 (the seed always creates the row).
--   phase <> 'VALUED_CUTOFF_PASSED' -> 422 "run the valued cutoff first" (nothing may still be
--     waiting to settle when the money is closed).
--   Otherwise business_date = row["business_date"] (the day being closed), continue.

-- name: lock_settlement_accounts
-- Lock EVERY settlement account, lowest account_id first (the global order). Deliveries and
-- payments are already held off by the sequencer; this also holds off a mint or burn, which locks
-- its own settlement account (without the sequencer) to change its collateral value.
-- From here on, no other request can change any of these rows until COMMIT.
SELECT account_id, net_cents, collateral_value_cents, net_debit_cap_cents
FROM settlement_accounts
ORDER BY account_id
FOR UPDATE;
-- APP DECIDES: keeps the number of rows; every later statement must cover all of them.

-- name: record_settlement_figures
-- DTC's final figures (about 3:45pm ET): one row per account with its net balance right now,
-- BEFORE it is set to 0 - the record of what each participant owed or was owed today.
-- INSERT ... SELECT: one statement copies every locked row; no loop in the app.
-- PRIMARY KEY (business_date, account_id): the same day can never get a second set of figures.
INSERT INTO settlement_figures (business_date, account_id, final_net_cents)
SELECT %(business_date)s, account_id, net_cents
FROM settlement_accounts
RETURNING account_id, final_net_cents;
-- APP DECIDES: rowcount must equal the number of locked settlement accounts.
--   RETURNING has no ORDER BY, so the app sorts the figures by account_id for the result.
--   e.g. 101 +3000000, 102 -1000000, 103 -1950000, 104 0.

-- name: record_nss_file
-- The simulated NSS file (about 4:30pm ET at DTC): ONE net-net line per settling bank, the SUM of
-- the nets of all accounts it settles for.  + the bank is paid   - the bank pays.
-- A data-modifying CTE: the INSERT runs inside WITH, its RETURNING rows become the table "nss",
-- and the outer SELECT joins them to settling_banks to add each bank's name.
--   SUM(...)::bigint: SUM of a BIGINT is NUMERIC in Postgres; cast back so the value is a whole
--   number of cents (psycopg would otherwise return a Decimal).
--   JOIN settling_banks (an INNER JOIN): an account with no settling bank is LEFT OUT here, so the
--   NSS file no longer adds up to the figures - reconciliation (check_closed_days and
--   check_cash_lines_net_to_zero) then fails loudly instead of the money being silently assigned.
--   GROUP BY b.id: one row per bank; a bank's line is written even when its net-net is 0.
-- PRIMARY KEY (business_date, settling_bank_id): one NSS file per day.
WITH nss AS (
  INSERT INTO nss_file_lines (business_date, settling_bank_id, net_net_cents)
  SELECT %(business_date)s, b.id, SUM(s.net_cents)::bigint
  FROM settlement_accounts s
  JOIN accounts a       ON a.id = s.account_id
  JOIN settling_banks b ON b.id = a.settling_bank_id
  GROUP BY b.id
  RETURNING settling_bank_id, net_net_cents
)
SELECT nss.settling_bank_id, b.bank_name, nss.net_net_cents
FROM nss
JOIN settling_banks b ON b.id = nss.settling_bank_id
ORDER BY nss.settling_bank_id;
-- APP DECIDES: any number of rows (one per bank with accounts); they come back by bank id.
--   e.g. 201 'Settling Bank One (demo)' +2000000, 202 'Settling Bank Two (demo)' -1950000.

-- name: post_closing_participant_entries
-- Close each participant's balance in the cash ledger: one PARTICIPANT_SETTLEMENT line of
-- -net_cents per account, so its lines for the day now add up to 0 (alpha's +3,000,000 credit
-- gets a -3,000,000 line: it is paid out through its settling bank's net-net figure).
-- WHERE net_cents <> 0: an account that is already flat gets no line (the CHECK amount_cents <> 0
-- forbids a line that changes nothing).
-- ORDER BY: the new lines get their ids in account order - easier to read, same totals.
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
SELECT %(operation_id)s, %(business_date)s, 'PARTICIPANT_SETTLEMENT', account_id, -net_cents
FROM settlement_accounts
WHERE net_cents <> 0
ORDER BY account_id;
-- APP DECIDES: rowcount must equal the number of figures with final_net_cents <> 0.

-- name: post_closing_fed_entries
-- The money the Fed moves: one DTC_FED_ACCOUNT line per NSS line of THIS day, amount
-- +net_net_cents (+ DTC's Fed account pays the bank out, - the bank pays DTC in; the same sign
-- rule as a settlement progress payment, whose Fed line is -amount: money in).
-- account_id NULL: a Fed line names no participant (the CHECK cash_line_shape requires it).
-- settling_bank_id: the bank this line pays or is paid by, so reconciliation can compare each
--   bank's Fed line with its NSS line (only a Fed line may name a bank: cash_line_bank_only_on_fed).
-- Read from nss_file_lines (just written above), so the cash ledger and the NSS file can never
-- disagree. business_date and settling_bank_id in the SELECT list are the NSS line's own columns
-- (business_date = today, by the WHERE).
-- WHERE net_net_cents <> 0: a bank with nothing to pay or receive gets no line.
-- Together with the participant lines, this operation's lines sum to ZERO:
--   -(sum of every net) + (sum of every bank's net-net) = 0,  e.g. -50,000 + 2,000,000 - 1,950,000
--   (true as long as every account has a settling bank - otherwise reconciliation fails).
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, settling_bank_id, amount_cents)
SELECT %(operation_id)s, business_date, 'DTC_FED_ACCOUNT', NULL, settling_bank_id, net_net_cents
FROM nss_file_lines
WHERE business_date = %(business_date)s
  AND net_net_cents <> 0
ORDER BY settling_bank_id;
-- APP DECIDES: rowcount must equal the number of NSS lines with net_net_cents <> 0.

-- name: zero_settlement_accounts
-- Every balance starts the next day flat. The money has been settled, so the stored balance now
-- matches its cash ledger lines again (their sum is 0).
-- An absolute SET (not net_cents + delta): the target IS 0, and 0 always passes both risk-control
-- CHECKs (0 >= -cap because cap >= 0; collateral + 0 >= 0 because collateral >= 0).
-- Safe because every row is locked: no request changed a net since record_settlement_figures.
-- WHERE net_cents <> 0: only rows that change; collateral and the cap are not touched.
UPDATE settlement_accounts
SET net_cents = 0
WHERE net_cents <> 0
RETURNING account_id;
-- APP DECIDES: rowcount must equal the number of figures with final_net_cents <> 0.

-- name: open_next_business_day
-- Move the sequencer row to the NEXT WEEKDAY and reopen valued processing.
-- EXTRACT(ISODOW ...) numbers the days Monday = 1 ... Saturday = 6, Sunday = 7.
--   Friday   (5) + 3 days -> Monday
--   Saturday (6) + 2 days -> Monday
--   any other day + 1     -> the next day (Sunday + 1 = Monday)
-- date + integer = a date that many days later.
-- Lab choice: weekends only; DTC also skips holidays (no holiday calendar is modelled), and the
-- collateral price is not refreshed with a new prior close.
-- UPDATED IN PLACE, never deleted and re-inserted: a deliver order already waiting on the
-- sequencer lock wakes up, re-reads THIS row and simply sees the new day, phase OPEN.
-- WHERE phase = 'VALUED_CUTOFF_PASSED' repeats the app's check in SQL (0 rows -> raise).
UPDATE settlement_day
SET business_date = business_date + CASE EXTRACT(ISODOW FROM business_date)
                                      WHEN 5 THEN 3
                                      WHEN 6 THEN 2
                                      ELSE 1
                                    END,
    phase = 'OPEN'
WHERE phase = 'VALUED_CUTOFF_PASSED'
RETURNING business_date;
-- APP DECIDES: rowcount must be 1, otherwise raise. RETURNING gives the new business date.

-- name: store_result
-- result = Jsonb(result_dict) - what the API returns, and what a replay returns
--   e.g. {"operationId": 61, "type": "FINAL_SETTLEMENT", "businessDate": "2026-10-19",
--         "nextBusinessDate": "2026-10-20", "figures": [...], "nssFile": [...]}
UPDATE operations
SET result = %(result)s
WHERE id = %(operation_id)s;
-- APP DECIDES: rowcount must be 1, otherwise raise. Then the block ends -> COMMIT -> 201.

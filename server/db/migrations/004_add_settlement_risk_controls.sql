-- 004_add_settlement_risk_controls.sql   (Lesson 11)
-- The CASH LEG: delivery-versus-payment (DVP) settlement with DTC-style risk controls.
-- Simulation only: modelled on public descriptions of DTC's settlement service, not affiliated with DTCC.
--
-- The idea in one sentence: securities move one delivery at a time, in real time, but the money is
-- NETTED: each participant ends the day with one net figure, and each settling bank pays or is paid
-- one "net-net" amount at the end of the day (apart from settlement progress payments, Fed funds a
-- participant may wire in during the day). Because the securities leave before the money arrives,
-- every delivery must first pass two controls:
--   Collateral Monitor (CM) = collateral value + net settlement balance   must stay >= 0
--     (a participant that fails to pay is always covered by collateral after haircuts)
--   Net Debit Cap (NDC):     net settlement balance >= -cap               (a debit never exceeds the cap)
-- A delivery that would break either control does not fail: it RECYCLES (waits) and is retried on
-- every credit; it settles once a credit makes enough room, and an order still waiting at the valued
-- cutoff is dropped and never settles.
--
-- Money is in whole CENTS as BIGINT: exact (no floating point), and every value this lab can reach
-- stays far below 2^53, so it is still an exact number in the JavaScript client.
-- Every SUM(...) of a BIGINT column is NUMERIC in Postgres (psycopg returns Decimal), so every query
-- casts its sums back with ::bigint - an overflow then fails loudly instead of returning a Decimal.
--
-- Rows in the new reference tables come from seeds/004_settlement_reference.sql, never from this file:
-- on the deployed database, accounts and positions already exist and keep changing, so the seed
-- computes each account's starting collateral from the positions it finds at that moment.
-- The runner wraps this whole file in one transaction: it applies completely, or not at all.


-- Part 1: a third role, OPERATOR - the simulated DTC settlement desk.
-- It runs the two end-of-day steps (valued cutoff, final settlement) and nothing else: it holds no
-- account, cannot deliver, convert or transfer, and (separation of duties) cannot run reconciliation.
-- A constraint cannot be edited in place, so we DROP it and ADD it again under the SAME name: the
-- error names the tests and the app look for stay the same.
ALTER TABLE users
  DROP CONSTRAINT users_role_check,
  DROP CONSTRAINT users_role_matches_account,
  ADD CONSTRAINT users_role_check CHECK (role IN ('PARTICIPANT', 'AUDITOR', 'OPERATOR')),
  ADD CONSTRAINT users_role_matches_account CHECK (
       (role = 'PARTICIPANT'             AND account_id IS NOT NULL)
    OR (role IN ('AUDITOR', 'OPERATOR')  AND account_id IS NULL)
  );


-- Part 2: settling banks. At DTC, end-of-day money moves through settling banks: a settling bank is a
-- bank participant that settles for itself and may settle for other participants, paying (or being
-- paid) one net-net amount for all of them. Each participant still owes its own balance: if its
-- settling bank refuses to settle for it, the participant must pay DTC directly via Fedwire.
-- Lab choice: every participant settles through one of two demo banks, and refusal is not modelled.
-- Ids 201, 202 are chosen by the seed (a range no other table uses, so a mixed-up id fails loudly).
CREATE TABLE settling_banks (
  id        INTEGER PRIMARY KEY,
  bank_name TEXT    NOT NULL UNIQUE
);

-- Which bank settles for each account. NULLable only because accounts already has rows when this
-- runs on the deployed database; the seed fills it in for every account. An account with no bank is
-- left out of the NSS file, and the reconciliation check of closed days then fails loudly.
ALTER TABLE accounts
  ADD COLUMN settling_bank_id INTEGER REFERENCES settling_banks (id);


-- Part 3: the settlement day. Exactly ONE row, ever:
--   singleton is the PRIMARY KEY and the CHECK only allows true, so a second row is impossible.
-- The row is also the SETTLEMENT SEQUENCER: every request that can settle or recycle a delivery
-- (deliver order, settlement progress payment, valued cutoff, final settlement) locks this row FIRST
-- (SELECT ... FOR UPDATE), so those requests run one at a time, in order - like one settlement engine.
-- At the end of the day the row is UPDATED in place (next business_date, phase OPEN), never deleted
-- and re-inserted: a request waiting on its lock then re-reads the same row instead of finding none.
CREATE TABLE settlement_day (
  singleton               BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  business_date           DATE    NOT NULL,
  phase                   TEXT    NOT NULL CHECK (phase IN ('OPEN', 'VALUED_CUTOFF_PASSED')),
  -- DTC's maximum net debit cap (no participant's cap may exceed it). $3.844B since 6 Apr 2026;
  -- this lab scales every dollar figure down by 100,000, so the seed stores $38,440.00.
  max_net_debit_cap_cents BIGINT  NOT NULL CHECK (max_net_debit_cap_cents >= 0)
);


-- Part 4: what one unit is worth AS COLLATERAL: yesterday's closing price minus a haircut.
-- haircut_bps: 1 bp = 0.01%. DTC's haircuts range from 2% to 100%, so 200..10000 bps.
-- collateral_cents_per_unit is a GENERATED column: Postgres computes it from the two columns on every
-- insert or update, so the haircut arithmetic exists in exactly one place and can never be stale.
-- BIGINT division truncates, which for positive numbers rounds DOWN: collateral is never overstated.
CREATE TABLE collateral_prices (
  asset_id                  INTEGER PRIMARY KEY REFERENCES assets (id),
  prior_close_cents         BIGINT  NOT NULL CHECK (prior_close_cents > 0),
  haircut_bps               INTEGER NOT NULL CHECK (haircut_bps BETWEEN 200 AND 10000),
  collateral_cents_per_unit BIGINT  GENERATED ALWAYS AS (prior_close_cents * (10000 - haircut_bps) / 10000) STORED
);


-- Part 5: the inputs of each account's net debit cap, so the cap is computed, not typed in:
--   cap = LEAST(maximum NDC, average of the 3 highest intraday net-debit peaks (70 business days) x factor)
-- factor_bps 10000..20000 = a factor between 1 and 2. The seed's numbers are illustrative.
CREATE TABLE net_debit_cap_inputs (
  account_id          INTEGER PRIMARY KEY REFERENCES accounts (id),
  avg_top3_peak_cents BIGINT  NOT NULL CHECK (avg_top3_peak_cents >= 0),
  factor_bps          INTEGER NOT NULL CHECK (factor_bps BETWEEN 10000 AND 20000)
);


-- Part 6: each account's settlement balance for the current day - the row the two controls guard.
--   net_cents > 0  credit: DTC owes the participant (it delivered securities and is waiting to be paid)
--   net_cents < 0  debit:  the participant owes DTC (it received securities and pays at the end of the day)
-- collateral_value_cents = traditional units x collateral_cents_per_unit. Tokenized units count ZERO:
--   under the SEC staff no-action letter of 11 Dec 2025, DTC gives tokenized entitlements no collateral
--   value. Stored (not computed in every query) so that the CHECKs below can judge it on every UPDATE.
-- The seed creates one row per account; requests only ever UPDATE them, using relative updates
-- (col = col + delta), so each CHECK judges the real new value.
CREATE TABLE settlement_accounts (
  account_id             INTEGER PRIMARY KEY REFERENCES accounts (id),
  net_cents              BIGINT  NOT NULL DEFAULT 0,
  collateral_value_cents BIGINT  NOT NULL CHECK (collateral_value_cents >= 0),
  net_debit_cap_cents    BIGINT  NOT NULL CHECK (net_debit_cap_cents >= 0),

  -- THE TWO RISK CONTROLS, as database rules. The app checks them first (to recycle with a clear
  -- reason); these are the guarantee: even an app bug cannot commit a breach.
  CONSTRAINT settlement_within_net_debit_cap
    CHECK (net_cents >= -net_debit_cap_cents),
  CONSTRAINT settlement_collateral_monitor_nonnegative
    CHECK (collateral_value_cents + net_cents >= 0)
);


-- Part 7: operations learns the cash leg.
-- New types and the shape each one must have (who acted, which accounts, units, money):
--   type                         actor          source      destination  asset+quantity  amount_cents
--   DELIVER_ORDER                the deliverer  deliverer   receiver     yes             yes
--   DVP_SETTLEMENT               NULL (system)  deliverer   receiver     yes             yes
--   SETTLEMENT_PROGRESS_PAYMENT  the payer      NULL        payer        no              yes
--   VALUED_CUTOFF                the operator   NULL        NULL         no              no
--   FINAL_SETTLEMENT             the operator   NULL        NULL         no              no
-- DELIVER_ORDER is the REQUEST; DVP_SETTLEMENT is the EVENT that moves units and money. They are
-- separate rows because a recycled order settles later - inside somebody else's request.
-- asset_id and quantity lose NOT NULL (a payment moves no units), so the shape CHECK below re-asserts
-- them for every type that moves units: the old types keep exactly the rules they had.
ALTER TABLE operations
  DROP CONSTRAINT operations_operation_type_check,
  DROP CONSTRAINT operations_columns_match_type,
  ALTER COLUMN asset_id DROP NOT NULL,
  ALTER COLUMN quantity DROP NOT NULL,
  -- Appended at the end, so every existing INSERT (which names its columns) is unaffected.
  ADD COLUMN amount_cents BIGINT
    CONSTRAINT operations_amount_cents_check CHECK (amount_cents BETWEEN 1 AND 10000000000000),
  ADD CONSTRAINT operations_operation_type_check CHECK (operation_type IN (
    'OPENING', 'CONVERT_TO_TOKENIZED', 'CONVERT_TO_TRADITIONAL', 'TRANSFER',
    'DELIVER_ORDER', 'DVP_SETTLEMENT', 'SETTLEMENT_PROGRESS_PAYMENT', 'VALUED_CUTOFF', 'FINAL_SETTLEMENT')),
  ADD CONSTRAINT operations_columns_match_type CHECK (
       (operation_type = 'OPENING'
          AND actor_user_id IS NULL     AND source_account_id IS NULL     AND destination_account_id IS NOT NULL
          AND asset_id IS NOT NULL      AND quantity IS NOT NULL          AND amount_cents IS NULL)
    OR (operation_type = 'TRANSFER'
          AND actor_user_id IS NOT NULL AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
          AND asset_id IS NOT NULL      AND quantity IS NOT NULL          AND amount_cents IS NULL)
    OR (operation_type IN ('CONVERT_TO_TOKENIZED', 'CONVERT_TO_TRADITIONAL')
          AND actor_user_id IS NOT NULL AND source_account_id IS NOT NULL AND destination_account_id IS NULL
          AND asset_id IS NOT NULL      AND quantity IS NOT NULL          AND amount_cents IS NULL)
    OR (operation_type = 'DELIVER_ORDER'
          AND actor_user_id IS NOT NULL AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
          AND asset_id IS NOT NULL      AND quantity IS NOT NULL          AND amount_cents IS NOT NULL)
    OR (operation_type = 'DVP_SETTLEMENT'
          AND actor_user_id IS NULL     AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
          AND asset_id IS NOT NULL      AND quantity IS NOT NULL          AND amount_cents IS NOT NULL)
    OR (operation_type = 'SETTLEMENT_PROGRESS_PAYMENT'
          AND actor_user_id IS NOT NULL AND source_account_id IS NULL     AND destination_account_id IS NOT NULL
          AND asset_id IS NULL          AND quantity IS NULL              AND amount_cents IS NOT NULL)
    OR (operation_type IN ('VALUED_CUTOFF', 'FINAL_SETTLEMENT')
          AND actor_user_id IS NOT NULL AND source_account_id IS NULL     AND destination_account_id IS NULL
          AND asset_id IS NULL          AND quantity IS NULL              AND amount_cents IS NULL)
  ),
  -- Idempotency for SYSTEM rows too. A plain UNIQUE treats every NULL as different from every other
  -- NULL, so two DVP_SETTLEMENT rows (actor NULL) with the same key would both be accepted.
  -- NULLS NOT DISTINCT (Postgres 15+) treats NULL actors as equal: the system key 'dvp-<order id>'
  -- can be used once, so one order can never settle twice. ON CONFLICT (actor_user_id,
  -- idempotency_key) still finds this constraint, so the existing reserve statements are unchanged.
  DROP CONSTRAINT operations_actor_key_unique,
  ADD CONSTRAINT operations_actor_key_unique UNIQUE NULLS NOT DISTINCT (actor_user_id, idempotency_key);


-- Part 8: one row per deliver order: what was instructed, and where it is in its life.
--   SETTLED            moved: units and money, under its DVP_SETTLEMENT operation
--   RECYCLING          waiting: it would break a control (recycle_reason says which); retried on every credit
--   DROPPED_AT_CUTOFF  still waiting at the valued cutoff, so it never settles (recycle_reason is kept)
-- This is the INDEPENDENT RECORD OF THE INSTRUCTION that the original reconciliation could not check:
-- reconciliation now compares every settled order with the ledger lines written for it.
CREATE TABLE delivery_instructions (
  id                      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  submitted_operation_id  INTEGER NOT NULL UNIQUE REFERENCES operations (id),   -- its DELIVER_ORDER row
  business_date           DATE    NOT NULL,
  deliverer_account_id    INTEGER NOT NULL REFERENCES accounts (id),
  receiver_account_id     INTEGER NOT NULL REFERENCES accounts (id),
  asset_id                INTEGER NOT NULL REFERENCES assets (id),
  quantity                INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 1000000),
  amount_cents            BIGINT  NOT NULL CHECK (amount_cents BETWEEN 1 AND 10000000000000),
  status                  TEXT    NOT NULL CHECK (status IN ('SETTLED', 'RECYCLING', 'DROPPED_AT_CUTOFF')),
  recycle_reason          TEXT    CHECK (recycle_reason IN (
                            'INSUFFICIENT_POSITION',          -- the deliverer lacks the units
                            'DELIVERER_COLLATERAL_MONITOR',   -- the deliverer's CM would go negative
                            'RECEIVER_COLLATERAL_MONITOR',    -- the receiver's CM would go negative
                            'RECEIVER_NET_DEBIT_CAP')),       -- the receiver's debit would exceed its cap
  settlement_operation_id INTEGER UNIQUE REFERENCES operations (id),             -- its DVP_SETTLEMENT row

  CONSTRAINT delivery_no_self_delivery CHECK (deliverer_account_id <> receiver_account_id),
  -- Settled <=> it has a settlement event. Both sides are TRUE/FALSE (never NULL), so this cannot
  -- pass by accident. UNIQUE on settlement_operation_id: one event settles at most one order.
  CONSTRAINT delivery_settled_has_operation CHECK ((status = 'SETTLED') = (settlement_operation_id IS NOT NULL)),
  -- Settled <=> no recycle reason. A waiting or dropped order always says why.
  CONSTRAINT delivery_reason_matches_status CHECK ((status = 'SETTLED') = (recycle_reason IS NULL))
);


-- Part 9: the cash ledger - the money twin of ledger_entries. Every line is a signed change:
--   PARTICIPANT_SETTLEMENT  a participant's settlement balance (+ credit, - debit); account_id is set
--   DTC_FED_ACCOUNT         money actually moving through DTC's account at the Fed; account_id is NULL
--                             a settlement progress payment in: -amount, settling_bank_id NULL
--                             end of day: one line per settling bank, +its net-net, settling_bank_id set
-- Every operation's lines sum to ZERO. Reconciliation checks it, checks that each stored net_cents
-- equals the sum of that account's lines, and compares each bank's end-of-day Fed line with its
-- line in the NSS file.
-- A separate table (not a 'CASH' representation in ledger_entries): units and cents are different
-- things, and ledger_entries keeps its rule that a line belongs to a position bucket.
CREATE TABLE cash_ledger_entries (
  id               INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  operation_id     INTEGER NOT NULL REFERENCES operations (id),
  business_date    DATE    NOT NULL,
  ledger           TEXT    NOT NULL CHECK (ledger IN ('PARTICIPANT_SETTLEMENT', 'DTC_FED_ACCOUNT')),
  account_id       INTEGER REFERENCES settlement_accounts (account_id),
  -- The settling bank an end-of-day Fed line pays (or is paid by). NULL on every other line: a
  -- payment's Fed line records only that the funds came in, not which bank wired them.
  settling_bank_id INTEGER REFERENCES settling_banks (id),
  amount_cents     BIGINT  NOT NULL CHECK (amount_cents <> 0),   -- a line that changes nothing is a bug

  -- A participant line names its account; a Fed line names none.
  CONSTRAINT cash_line_shape CHECK ((ledger = 'PARTICIPANT_SETTLEMENT') = (account_id IS NOT NULL)),
  -- Only a Fed line may name a settling bank (a participant line already names its account).
  CONSTRAINT cash_line_bank_only_on_fed CHECK (settling_bank_id IS NULL OR ledger = 'DTC_FED_ACCOUNT')
);


-- Part 10: what final settlement leaves behind for each closed day.
-- settlement_figures: each account's final net, just before it is settled to zero.
-- nss_file_lines: the simulated NSS file - one net-net figure per settling bank (+ the bank is paid,
--   - the bank pays). A table, not a Federal Reserve interface.
-- A day can close only once: the PRIMARY KEYs make a second set of figures for the same day fail.
CREATE TABLE settlement_figures (
  business_date   DATE    NOT NULL,
  account_id      INTEGER NOT NULL REFERENCES settlement_accounts (account_id),
  final_net_cents BIGINT  NOT NULL,
  PRIMARY KEY (business_date, account_id)
);

CREATE TABLE nss_file_lines (
  business_date    DATE    NOT NULL,
  settling_bank_id INTEGER NOT NULL REFERENCES settling_banks (id),
  net_net_cents    BIGINT  NOT NULL,
  PRIMARY KEY (business_date, settling_bank_id)
);

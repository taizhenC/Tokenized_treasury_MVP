-- 004_settlement_reference.sql   (Lesson 11 - DEMO SEED DATA, simulation only)
-- Reference data for the cash leg, plus the operator login.
-- Every dollar figure is DTC-shaped but scaled down by 100,000 and illustrative: the formulas follow
-- DTC's published Settlement Service Guide, the numbers are invented.
-- Runs once, after seeds 001-003 (filename order). On the deployed database it runs against accounts
-- and positions that may already have changed, so it COMPUTES every starting value from what it finds.

-- Step 1: two settling banks. Alpha and Beta settle through Bank One; Gamma and Delta through Bank Two.
INSERT INTO settling_banks (id, bank_name)
VALUES
  (201, 'Settling Bank One (demo)'),
  (202, 'Settling Bank Two (demo)');

UPDATE accounts
SET settling_bank_id = CASE WHEN id IN (101, 102) THEN 201 ELSE 202 END;

-- Step 2: the one settlement-day row: the first business day on or after today, open for valued
-- processing. A business day is a weekday - the same rule as open_next_business_day in
-- final_settlement.sql, with no holiday calendar:
--   EXTRACT(ISODOW ...) numbers the days Monday = 1 ... Saturday = 6, Sunday = 7
--   Saturday + 2 days -> Monday,  Sunday + 1 day -> Monday,  a weekday + 0 -> today
--   date + integer = a date that many days later.
-- CURRENT_DATE is today in the database session's TimeZone setting (UTC in the lab's Docker
-- Postgres, and by default on managed Postgres such as Render's), not New York time: seeded on a
-- Friday evening ET, it is already Saturday here, so the first business day is the Monday.
-- Maximum NDC: DTC's $3,844,000,000 / 100,000 = $38,440.00 = 3,844,000 cents.
INSERT INTO settlement_day (business_date, phase, max_net_debit_cap_cents)
VALUES (CURRENT_DATE + CASE EXTRACT(ISODOW FROM CURRENT_DATE)
                         WHEN 6 THEN 2
                         WHEN 7 THEN 1
                         ELSE 0
                       END,
        'OPEN', 3844000);

-- Step 3: DEMO-TREASURY as collateral: prior close $100.00, haircut 5% (500 bps)
-- -> collateral_cents_per_unit = 10000 * 9500 / 10000 = 9500 ($95.00), computed by Postgres.
INSERT INTO collateral_prices (asset_id, prior_close_cents, haircut_bps)
VALUES (1, 10000, 500);

-- Step 4: net debit cap inputs (illustrative). cap = LEAST(max, avg x factor):
--   alpha  $32,000 x 1.25 = $40,000 -> capped at the maximum, $38,440
--   beta   $10,000 x 1.50 = $15,000
--   gamma  $20,000 x 1.25 = $25,000
--   Delta  $0 x 1.00 = $0, a lab choice so that Delta can never be debited (DTC would apply its
--          established minimum net debit cap; this lab has no minimum)
INSERT INTO net_debit_cap_inputs (account_id, avg_top3_peak_cents, factor_bps)
VALUES
  (101, 3200000, 12500),
  (102, 1000000, 15000),
  (103, 2000000, 12500),
  (104,       0, 10000);

-- Step 5: one settlement account per account, all computed in ONE statement:
--   net_cents              0: a new day starts flat
--   collateral_value_cents traditional units x collateral per unit (tokenized units count zero)
--   net_debit_cap_cents    LEAST(maximum NDC, avg x factor); BIGINT division rounds down
-- INSERT ... SELECT reads the CURRENT positions, so it is right on a fresh database and on one that
-- has been used. (One instrument: one position row per account.)
INSERT INTO settlement_accounts (account_id, net_cents, collateral_value_cents, net_debit_cap_cents)
SELECT p.account_id,
       0,
       p.traditional_units * c.collateral_cents_per_unit,
       LEAST(d.max_net_debit_cap_cents, n.avg_top3_peak_cents * n.factor_bps / 10000)
FROM positions p
JOIN collateral_prices    c ON c.asset_id = p.asset_id
JOIN net_debit_cap_inputs n ON n.account_id = p.account_id
CROSS JOIN settlement_day d;

-- Step 6: the operator login - the simulated settlement desk (no account).
-- Same DEV-ONLY bcrypt hash as seeds/002_users.sql: password  demo-password-1
INSERT INTO users (id, username, password_hash, role, account_id)
VALUES (15, 'operator', '$2b$10$HlFur6huP5jxpqqw2wExGeY9qnG33WN/qZ8lrC4UbfZ2r1.XezZCm', 'OPERATOR', NULL);

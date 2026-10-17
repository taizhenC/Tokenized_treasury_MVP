-- 001_reference_data.sql   (Lesson 4 - DEV SEED DATA, simulation only: no real securities, firms or wallets)
-- One instrument and four accounts, with ids chosen by hand.
-- Id ranges: asset 1, users 11-14, accounts 101-104 - deliberately different, so an id passed
-- in the wrong place gives a foreign-key error or 0 rows instead of silently "working".
-- Accounts 101-103 are approved and each gets a participant login (seeds/002_users.sql).
-- Account 104 (Delta) is deliberately NOT approved and has no login: it exists only so the
-- demo can show "transfer to an unapproved wallet is rejected".

-- 3 approved accounts x 1,000 opening units each = 3,000 units held by the custodian.
INSERT INTO assets (id, symbol, total_custodied_units, unit_precision)
VALUES (1, 'DEMO-TREASURY', 3000, 0);

INSERT INTO accounts (id, participant_name, wallet_ref, is_approved)
VALUES
  (101, 'Participant Alpha (demo)',               'SIM-WALLET-ALPHA', true),
  (102, 'Participant Beta (demo)',                'SIM-WALLET-BETA',  true),
  (103, 'Participant Gamma (demo)',               'SIM-WALLET-GAMMA', true),
  (104, 'Participant Delta (demo, NOT approved)', 'SIM-WALLET-DELTA', false);

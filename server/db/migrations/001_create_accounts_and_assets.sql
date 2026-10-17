-- 001_create_accounts_and_assets.sql (Lesson 1)
-- The two simplest tables: only PRIMARY KEY, NOT NULL, DEFAULT and UNIQUE.
-- Foreign keys and CHECK rules come in Lesson 2.
-- Rows in both tables are only ever created by seed files, so we choose the ids ourselves.
-- Id ranges never overlap (asset = 1 digit, user = 2 digits, account = 3 digits), so an id
-- passed in the wrong place fails loudly instead of quietly "working".
-- The runner wraps this whole file in one transaction, so there is no BEGIN/COMMIT here.

-- One row per participant account = one simulated, registered "wallet".
CREATE TABLE accounts (
  id               INTEGER PRIMARY KEY,               -- 101, 102, ... chosen by the seed
  participant_name TEXT    NOT NULL,
  wallet_ref       TEXT    NOT NULL UNIQUE,           -- synthetic label such as 'SIM-WALLET-ALPHA', never a blockchain address
  is_approved      BOOLEAN NOT NULL DEFAULT false     -- approved (registered) wallet? Deny by default.
);

-- The instrument. The MVP has exactly one: DEMO-TREASURY (a simulation, not a real security).
-- total_custodied_units is the custodian's total; reconciliation later checks that
-- everyone's traditional + tokenized units add up to exactly this number.
-- Units are whole numbers. INTEGER (max about 2.1 billion) is plenty, and every INTEGER is
-- safe in the JavaScript browser client (a very large BIGINT would not be).
CREATE TABLE assets (
  id                    INTEGER PRIMARY KEY,          -- 1, chosen by the seed
  symbol                TEXT    NOT NULL UNIQUE,      -- 'DEMO-TREASURY'
  total_custodied_units INTEGER NOT NULL,
  unit_precision        INTEGER NOT NULL DEFAULT 0    -- decimal places; 0 = whole units only
);

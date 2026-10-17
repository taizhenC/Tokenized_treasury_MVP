-- 002_create_users_and_positions.sql (Lesson 2)
-- Foreign keys, CHECK rules and a two-column (composite) primary key.

-- Part 1: rules for assets.
-- assets was created by migration 001, which is ALREADY APPLIED (it has a row in schema_migrations),
-- so we never edit 001: the runner would skip it. Schema changes always go in a NEW migration.
-- Postgres names these assets_total_custodied_units_check and assets_unit_precision_check.
ALTER TABLE assets
  ADD CHECK (total_custodied_units >= 0),
  ADD CHECK (unit_precision = 0);            -- this MVP supports whole units only

-- Part 2: people who log in.
--   PARTICIPANT: linked to exactly one account and may act only on that account.
--   AUDITOR:     linked to no account; reads everything, changes nothing.
-- Rows come only from seed files, so the ids are chosen by hand (11, 12, ...).
-- (There is no sessions table: the FastAPI app keeps the logged-in user id in a signed
--  cookie and reloads this row on every request.)
CREATE TABLE users (
  id            INTEGER PRIMARY KEY,
  username      TEXT    NOT NULL UNIQUE,
  password_hash TEXT    NOT NULL,                       -- bcrypt hash, never the password itself
  role          TEXT    NOT NULL CHECK (role IN ('PARTICIPANT', 'AUDITOR')),
  account_id    INTEGER REFERENCES accounts (id),       -- NULL for the auditor

  -- A named CHECK gives a readable error name instead of the generated "users_check".
  CONSTRAINT users_role_matches_account CHECK (
       (role = 'PARTICIPANT' AND account_id IS NOT NULL)
    OR (role = 'AUDITOR'     AND account_id IS NULL)
  )
);

-- Part 3: what each account holds of each asset, in both forms, side by side.
-- Every conversion and transfer locks these rows (SELECT ... FOR UPDATE) and then UPDATEs them.
-- PRIMARY KEY (account_id, asset_id) = UNIQUE + NOT NULL on the pair:
--   exactly one position row per account per asset, and it is the key we look rows up by.
-- The seed creates every row up front, so requests only ever UPDATE existing rows.
CREATE TABLE positions (
  account_id        INTEGER REFERENCES accounts (id),
  asset_id          INTEGER REFERENCES assets (id),
  traditional_units INTEGER NOT NULL CHECK (traditional_units >= 0),  -- no negative balances,
  tokenized_units   INTEGER NOT NULL CHECK (tokenized_units >= 0),    -- even if the app has a bug
  PRIMARY KEY (account_id, asset_id)
);

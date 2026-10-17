-- 003_opening_balances.sql   (Lesson 4 - DEV SEED DATA)
-- Opening allocations are recorded as real ledger events, not just typed into positions,
-- so BOTH reconciliation checks pass from the very first moment:
--   (1) SUM(traditional + tokenized) over all accounts = total_custodied_units (3000)
--   (2) every stored position = SUM of its ledger lines
-- Statement order follows the foreign keys: positions before ledger_entries.
-- RUN THIS FILE ONLY ONCE, through the runner. To start over: drop and recreate the database,
-- then run the runner again (npm run db:reset). If you ever pipe a file by hand, use
--   psql -1 -v ON_ERROR_STOP=1 -f <file>
-- so a failure undoes the whole file.

-- Step 1: pre-create every position row (Delta's too, at zero), so requests only ever UPDATE.
INSERT INTO positions (account_id, asset_id, traditional_units, tokenized_units)
VALUES
  (101, 1, 1000, 0),
  (102, 1, 1000, 0),
  (103, 1, 1000, 0),
  (104, 1,    0, 0);

-- Step 2: one OPENING operation per funded account. The seed did this, not a person,
-- so actor_user_id is NULL and source is NULL (operations_columns_match_type requires that shape).
-- Delta gets none: it opens with 0 units, and quantity must be at least 1.
INSERT INTO operations
  (operation_type, actor_user_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES
  ('OPENING', NULL, 101, 1, 1000, 'opening-account-101', 'seed'),
  ('OPENING', NULL, 102, 1, 1000, 'opening-account-102', 'seed'),
  ('OPENING', NULL, 103, 1, 1000, 'opening-account-103', 'seed');

-- Step 3: each OPENING operation posts one +quantity line to the TRADITIONAL bucket.
-- INSERT ... SELECT copies EVERY OPENING row in the table. On a fresh database those are exactly
-- the 3 rows above - which is why this file must never run twice (it would double the ledger).
-- Copying the ids with SELECT means no identity value is ever typed by hand.
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
SELECT id, destination_account_id, asset_id, 'TRADITIONAL', quantity
FROM operations
WHERE operation_type = 'OPENING';

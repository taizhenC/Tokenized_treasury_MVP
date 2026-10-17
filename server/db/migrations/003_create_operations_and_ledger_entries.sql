-- 003_create_operations_and_ledger_entries.sql   (Lesson 3)
-- The business events and the unit-by-unit history. These are the only tables the APP inserts
-- into, so they are the only ones where the DATABASE makes the id (GENERATED ALWAYS AS IDENTITY).

-- One row per COMMITTED business event. The row is also the idempotency record:
-- each request inserts it FIRST (reserving actor + key) and fills "result" just before COMMIT.
-- A rejected request is rolled back, so its row is never saved.
--
-- Which columns each type fills (enforced by operations_columns_match_type below):
--   type                     actor        source        destination
--   OPENING                  NULL (seed)  NULL          the account
--   CONVERT_TO_TOKENIZED     the user     the account   NULL
--   CONVERT_TO_TRADITIONAL   the user     the account   NULL
--   TRANSFER                 the user     sender        receiver
-- Column order: actor -> account(s) (source before destination) -> asset -> quantity -> key -> hash,
-- the same order every INSERT lists them in. The app passes NAMED parameters (psycopg), so this
-- order is for reading, not for correctness: each value is matched by its name.
CREATE TABLE operations (
  id                     INTEGER     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  operation_type         TEXT        NOT NULL CHECK (operation_type IN
                           ('OPENING', 'CONVERT_TO_TOKENIZED', 'CONVERT_TO_TRADITIONAL', 'TRANSFER')),
  actor_user_id          INTEGER     REFERENCES users (id),      -- who asked; NULL only for seeded OPENING rows
  source_account_id      INTEGER     REFERENCES accounts (id),   -- account that gives up / converts units
  destination_account_id INTEGER     REFERENCES accounts (id),   -- account that receives units
  asset_id               INTEGER     NOT NULL REFERENCES assets (id),
  quantity               INTEGER     NOT NULL CHECK (quantity BETWEEN 1 AND 1000000),
  idempotency_key        TEXT        NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 100),  -- chosen by the client
  request_hash           TEXT        NOT NULL,                   -- sha256 of the canonical request
  result                 JSONB,                                  -- response to replay; set just before COMMIT
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),     -- transaction START time, not commit time

  -- Idempotency: one actor can use one key once. A concurrent duplicate waits on this index.
  CONSTRAINT operations_actor_key_unique UNIQUE (actor_user_id, idempotency_key),

  -- The same OR-of-ANDs pattern as users_role_matches_account: each type has exactly one allowed
  -- shape. IS NULL / IS NOT NULL are always TRUE or FALSE (never NULL), so this check never
  -- "passes by accident". It also keeps a conversion out of anybody else's history.
  CONSTRAINT operations_columns_match_type CHECK (
       (operation_type = 'OPENING'
          AND actor_user_id IS NULL     AND source_account_id IS NULL     AND destination_account_id IS NOT NULL)
    OR (operation_type = 'TRANSFER'
          AND actor_user_id IS NOT NULL AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL)
    OR (operation_type IN ('CONVERT_TO_TOKENIZED', 'CONVERT_TO_TRADITIONAL')
          AND actor_user_id IS NOT NULL AND source_account_id IS NOT NULL AND destination_account_id IS NULL)
  ),

  -- No transfer to yourself. The check above guarantees a TRANSFER has both sides, so for a
  -- transfer this is TRUE or FALSE. For other types one side is NULL, the comparison is NULL,
  -- and a CHECK only rejects FALSE - so those rows pass, which is what we want.
  CONSTRAINT operations_no_self_transfer CHECK (source_account_id <> destination_account_id)
);

-- The append-only history of every unit movement. Each line is a signed change to ONE bucket
-- (account, asset, representation): positive = units in, negative = units out.
-- Reconciliation check (2): for every bucket, stored position = SUM(units_delta).
CREATE TABLE ledger_entries (
  id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  operation_id   INTEGER NOT NULL REFERENCES operations (id),
  account_id     INTEGER NOT NULL,
  asset_id       INTEGER NOT NULL,
  representation TEXT    NOT NULL CHECK (representation IN ('TRADITIONAL', 'TOKENIZED')),
  units_delta    INTEGER NOT NULL CHECK (units_delta <> 0),   -- a line that changes nothing is a bug

  -- Two-column foreign key: a line can only post to a position row that exists.
  -- Together with the representation CHECK, every line belongs to exactly one of the
  -- (position, representation) buckets that reconciliation lists - so check (2) never misses one.
  CONSTRAINT ledger_entries_position_fk
    FOREIGN KEY (account_id, asset_id) REFERENCES positions (account_id, asset_id)
);

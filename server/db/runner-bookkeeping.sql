-- runner-bookkeeping.sql (Lesson 1)
--
-- The migration runner (server/db/migrate.py) looks these three blocks up by name.
-- Each block is a "-- name: ..." line followed by exactly ONE statement ending in ";".
-- Keep the name lines exactly as they are. No BEGIN/COMMIT: the runner handles transactions.
--
-- What the runner does with them (npm run db:migrate):
--   1. create_schema_migrations, once at start
--   2. list_applied_files, to learn which files already ran
--   3. for each .sql file NOT in that list, in filename order, ONE transaction:
--        the whole file, then record_applied_file for it
--      so a file and its bookkeeping row are saved together, or not at all.

-- name: create_schema_migrations
-- (a) Safe to run on every start: IF NOT EXISTS skips it when the table is already there.
--     filename is the PRIMARY KEY, so the same file can never be recorded twice.
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename   TEXT        PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- name: list_applied_files
-- (b) Which files already ran? The runner skips every name in this list.
SELECT filename
FROM schema_migrations
ORDER BY filename;

-- name: record_applied_file
-- (c) Remember one file. The runner fills the placeholder with the folder plus file name,
--     e.g. 'migrations/001_create_accounts_and_assets.sql':
--         conn.execute(sql, {"filename": name})
--     applied_at is left out on purpose, so its DEFAULT (now()) fills it in.
INSERT INTO schema_migrations (filename)
VALUES (%(filename)s);

-- admin.sql - used by tests/reset_db.py on the maintenance database ("postgres").
-- {database} is filled in with psycopg.sql.Identifier (a quoted name, never pasted text).

-- name: drop_database
DROP DATABASE IF EXISTS {database} WITH (FORCE);

-- name: create_database
CREATE DATABASE {database};

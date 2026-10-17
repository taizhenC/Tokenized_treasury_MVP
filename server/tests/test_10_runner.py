"""Lesson 1-3: the migration runner (server/db/migrate.py) and the schema it built.

tests/reset_db.py already ran the runner three times on an EMPTY database: migrations only,
then --seed, then --seed again (a no-op). These tests check what that produced.
"""

import os
import subprocess
import sys

import pytest

from db import migrate
from tests.safety import SERVER_DIR

EXPECTED_FILES = [
    "migrations/001_create_accounts_and_assets.sql",
    "migrations/002_create_users_and_positions.sql",
    "migrations/003_create_operations_and_ledger_entries.sql",
    "seeds/001_reference_data.sql",
    "seeds/002_users.sql",
    "seeds/003_opening_balances.sql",
]

EXPECTED_CONSTRAINTS = {
    ("accounts", "accounts_pkey"), ("accounts", "accounts_wallet_ref_key"),
    ("assets", "assets_pkey"), ("assets", "assets_symbol_key"),
    ("assets", "assets_total_custodied_units_check"), ("assets", "assets_unit_precision_check"),
    ("users", "users_pkey"), ("users", "users_username_key"), ("users", "users_role_check"),
    ("users", "users_role_matches_account"), ("users", "users_account_id_fkey"),
    ("positions", "positions_pkey"), ("positions", "positions_account_id_fkey"),
    ("positions", "positions_asset_id_fkey"), ("positions", "positions_traditional_units_check"),
    ("positions", "positions_tokenized_units_check"),
    ("operations", "operations_pkey"), ("operations", "operations_operation_type_check"),
    ("operations", "operations_quantity_check"), ("operations", "operations_idempotency_key_check"),
    ("operations", "operations_actor_key_unique"), ("operations", "operations_columns_match_type"),
    ("operations", "operations_no_self_transfer"), ("operations", "operations_actor_user_id_fkey"),
    ("operations", "operations_source_account_id_fkey"), ("operations", "operations_destination_account_id_fkey"),
    ("operations", "operations_asset_id_fkey"),
    ("ledger_entries", "ledger_entries_pkey"), ("ledger_entries", "ledger_entries_operation_id_fkey"),
    ("ledger_entries", "ledger_entries_position_fk"), ("ledger_entries", "ledger_entries_representation_check"),
    ("ledger_entries", "ledger_entries_units_delta_check"),
    ("schema_migrations", "schema_migrations_pkey"),
}


def test_bookkeeping_rows_are_folder_prefixed_and_timestamped(probe):
    rows = probe.rows("schema_migrations")
    assert [r["filename"] for r in rows] == EXPECTED_FILES
    assert all(r["has_time"] for r in rows)


def test_second_run_is_a_no_op(database_url):
    env = {**os.environ, "DATABASE_URL": database_url}
    done = subprocess.run(
        [sys.executable, "-m", "db.migrate", "--seed"], cwd=SERVER_DIR, env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "Database is up to date."


def test_a_failing_file_is_rolled_back_with_its_bookkeeping_row(probe, tmp_path, monkeypatch, capsys):
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "999_broken.sql").write_text(
        "CREATE TABLE should_not_exist (id integer);\nSELECT 1/0;\n", encoding="utf-8"
    )
    monkeypatch.setattr(migrate, "DB_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["migrate"])
    with pytest.raises(SystemExit) as stopped:
        migrate.main()
    assert stopped.value.code != 0
    assert "migrations/999_broken.sql failed and was rolled back" in str(stopped.value.code)
    assert "division by zero" in str(stopped.value.code)
    assert probe.value("table_exists", name="public.should_not_exist") is False
    assert "migrations/999_broken.sql" not in [r["filename"] for r in probe.rows("schema_migrations")]


def test_runner_reads_the_bookkeeping_blocks_from_its_sql_file():
    blocks = migrate.load_bookkeeping()
    assert set(migrate.BOOKKEEPING_BLOCKS) <= set(blocks)
    assert "%(filename)s" in blocks["record_applied_file"]


def test_seven_tables(probe):
    assert [r["table_name"] for r in probe.rows("tables")] == [
        "accounts", "assets", "ledger_entries", "operations", "positions", "schema_migrations", "users",
    ]


def test_all_33_constraints_by_name(probe):
    found = {(r["table_name"], r["conname"]) for r in probe.rows("constraints")}
    assert found == EXPECTED_CONSTRAINTS
    assert len(found) == 33


def column_map(probe, table):
    return {r["column_name"]: r for r in probe.rows("columns", table_name=table)}


def test_lesson1_columns_and_defaults(probe):
    sm = column_map(probe, "schema_migrations")
    assert sm["filename"]["data_type"] == "text" and sm["filename"]["is_nullable"] == "NO"
    assert sm["applied_at"]["data_type"] == "timestamp with time zone"
    assert sm["applied_at"]["column_default"] == "now()"
    assert column_map(probe, "accounts")["is_approved"]["column_default"] == "false"
    assert column_map(probe, "assets")["unit_precision"]["column_default"] == "0"


def test_lesson3_operations_column_order_and_identity(probe):
    cols = probe.rows("columns", table_name="operations")
    assert [c["column_name"] for c in cols] == [
        "id", "operation_type", "actor_user_id", "source_account_id", "destination_account_id", "asset_id",
        "quantity", "idempotency_key", "request_hash", "result", "created_at",
    ]
    assert cols[0]["is_identity"] == "YES" and cols[0]["identity_generation"] == "ALWAYS"
    assert {c["column_name"]: c["data_type"] for c in cols}["result"] == "jsonb"


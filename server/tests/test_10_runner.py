"""Lessons 1-3 and 11: the migration runner (server/db/migrate.py) and the schema it built.

tests/reset_db.py already ran the runner three times on an EMPTY database: migrations only,
then --seed, then --seed again (a no-op). These tests check what that produced.
(Migration 004 applied to a database that is already in use: test_15_upgrade_path.)
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
    "migrations/004_add_settlement_risk_controls.sql",
    "seeds/001_reference_data.sql",
    "seeds/002_users.sql",
    "seeds/003_opening_balances.sql",
    "seeds/004_settlement_reference.sql",
]

LESSON_1_3_CONSTRAINTS = {
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

# Migration 004. (users_role_check, users_role_matches_account, operations_operation_type_check,
# operations_columns_match_type and operations_actor_key_unique were dropped and added again under
# the SAME names, so they are in the set above and not here.)
LESSON_11_CONSTRAINTS = {
    ("accounts", "accounts_settling_bank_id_fkey"),
    ("operations", "operations_amount_cents_check"),
    ("settling_banks", "settling_banks_pkey"), ("settling_banks", "settling_banks_bank_name_key"),
    ("settlement_day", "settlement_day_pkey"), ("settlement_day", "settlement_day_singleton_check"),
    ("settlement_day", "settlement_day_phase_check"),
    ("settlement_day", "settlement_day_max_net_debit_cap_cents_check"),
    ("collateral_prices", "collateral_prices_pkey"), ("collateral_prices", "collateral_prices_asset_id_fkey"),
    ("collateral_prices", "collateral_prices_prior_close_cents_check"),
    ("collateral_prices", "collateral_prices_haircut_bps_check"),
    ("net_debit_cap_inputs", "net_debit_cap_inputs_pkey"),
    ("net_debit_cap_inputs", "net_debit_cap_inputs_account_id_fkey"),
    ("net_debit_cap_inputs", "net_debit_cap_inputs_avg_top3_peak_cents_check"),
    ("net_debit_cap_inputs", "net_debit_cap_inputs_factor_bps_check"),
    ("settlement_accounts", "settlement_accounts_pkey"),
    ("settlement_accounts", "settlement_accounts_account_id_fkey"),
    ("settlement_accounts", "settlement_accounts_collateral_value_cents_check"),
    ("settlement_accounts", "settlement_accounts_net_debit_cap_cents_check"),
    ("settlement_accounts", "settlement_within_net_debit_cap"),
    ("settlement_accounts", "settlement_collateral_monitor_nonnegative"),
    ("delivery_instructions", "delivery_instructions_pkey"),
    ("delivery_instructions", "delivery_instructions_submitted_operation_id_key"),
    ("delivery_instructions", "delivery_instructions_submitted_operation_id_fkey"),
    ("delivery_instructions", "delivery_instructions_deliverer_account_id_fkey"),
    ("delivery_instructions", "delivery_instructions_receiver_account_id_fkey"),
    ("delivery_instructions", "delivery_instructions_asset_id_fkey"),
    ("delivery_instructions", "delivery_instructions_quantity_check"),
    ("delivery_instructions", "delivery_instructions_amount_cents_check"),
    ("delivery_instructions", "delivery_instructions_status_check"),
    ("delivery_instructions", "delivery_instructions_recycle_reason_check"),
    ("delivery_instructions", "delivery_instructions_settlement_operation_id_key"),
    ("delivery_instructions", "delivery_instructions_settlement_operation_id_fkey"),
    ("delivery_instructions", "delivery_no_self_delivery"),
    ("delivery_instructions", "delivery_settled_has_operation"),
    ("delivery_instructions", "delivery_reason_matches_status"),
    ("cash_ledger_entries", "cash_ledger_entries_pkey"),
    ("cash_ledger_entries", "cash_ledger_entries_operation_id_fkey"),
    ("cash_ledger_entries", "cash_ledger_entries_ledger_check"),
    ("cash_ledger_entries", "cash_ledger_entries_account_id_fkey"),
    ("cash_ledger_entries", "cash_ledger_entries_amount_cents_check"), ("cash_ledger_entries", "cash_line_shape"),
    ("cash_ledger_entries", "cash_ledger_entries_settling_bank_id_fkey"),
    ("cash_ledger_entries", "cash_line_bank_only_on_fed"),
    ("settlement_figures", "settlement_figures_pkey"), ("settlement_figures", "settlement_figures_account_id_fkey"),
    ("nss_file_lines", "nss_file_lines_pkey"), ("nss_file_lines", "nss_file_lines_settling_bank_id_fkey"),
}

EXPECTED_CONSTRAINTS = LESSON_1_3_CONSTRAINTS | LESSON_11_CONSTRAINTS


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


def test_sixteen_tables(probe):
    assert [r["table_name"] for r in probe.rows("tables")] == [
        "accounts", "assets", "cash_ledger_entries", "collateral_prices", "delivery_instructions",
        "ledger_entries", "net_debit_cap_inputs", "nss_file_lines", "operations", "positions",
        "schema_migrations", "settlement_accounts", "settlement_day", "settlement_figures", "settling_banks",
        "users",
    ]


def test_all_82_constraints_by_name(probe):
    found = {(r["table_name"], r["conname"]) for r in probe.rows("constraints")}
    assert found - EXPECTED_CONSTRAINTS == set(), "a constraint this test does not know about"
    assert EXPECTED_CONSTRAINTS - found == set(), "a constraint is missing (or has another name)"
    assert len(LESSON_1_3_CONSTRAINTS) == 33 and len(LESSON_11_CONSTRAINTS) == 49
    assert len(found) == 82


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
        "amount_cents",  # Lesson 11: appended at the END, so every existing INSERT (it names its columns) still works
    ]
    assert cols[0]["is_identity"] == "YES" and cols[0]["identity_generation"] == "ALWAYS"
    assert {c["column_name"]: c["data_type"] for c in cols}["result"] == "jsonb"


def test_lesson11_operations_columns_that_a_payment_leaves_empty(probe):
    """A settlement progress payment moves no units, so asset_id and quantity lost NOT NULL;
    operations_columns_match_type re-asserts them for every type that does move units."""
    ops = column_map(probe, "operations")
    assert (ops["asset_id"]["is_nullable"], ops["quantity"]["is_nullable"]) == ("YES", "YES")
    assert (ops["amount_cents"]["data_type"], ops["amount_cents"]["is_nullable"]) == ("bigint", "YES")
    assert ops["operation_type"]["is_nullable"] == "NO" and ops["idempotency_key"]["is_nullable"] == "NO"


def test_lesson11_collateral_per_unit_is_a_generated_column(probe):
    """Postgres computes it from the price and the haircut on every write: one formula, never stale."""
    column = column_map(probe, "collateral_prices")["collateral_cents_per_unit"]
    assert column["is_generated"] == "ALWAYS"
    assert column["generation_expression"] == "((prior_close_cents * (10000 - haircut_bps)) / 10000)"
    assert column["data_type"] == "bigint"
    assert column_map(probe, "collateral_prices")["haircut_bps"]["is_generated"] == "NEVER"


def test_lesson11_every_money_column_is_whole_cents_as_bigint(probe):
    rows = probe.rows("money_columns")
    assert [(r["table_name"], r["column_name"]) for r in rows] == [
        ("cash_ledger_entries", "amount_cents"),
        ("collateral_prices", "prior_close_cents"),
        ("delivery_instructions", "amount_cents"),
        ("net_debit_cap_inputs", "avg_top3_peak_cents"),
        ("nss_file_lines", "net_net_cents"),
        ("operations", "amount_cents"),
        ("settlement_accounts", "collateral_value_cents"),
        ("settlement_accounts", "net_cents"),
        ("settlement_accounts", "net_debit_cap_cents"),
        ("settlement_day", "max_net_debit_cap_cents"),
        ("settlement_figures", "final_net_cents"),
    ]
    assert {r["data_type"] for r in rows} == {"bigint"}


def test_lesson11_defaults(probe):
    assert column_map(probe, "settlement_accounts")["net_cents"]["column_default"] == "0"  # a day starts flat
    assert column_map(probe, "settlement_day")["singleton"]["column_default"] == "true"
    assert column_map(probe, "accounts")["settling_bank_id"]["is_nullable"] == "YES"  # existing rows, then the seed
    # Only a closing Fed line names its settling bank (cash_line_bank_only_on_fed); every other line has NULL.
    assert column_map(probe, "cash_ledger_entries")["settling_bank_id"]["is_nullable"] == "YES"


def test_lesson11_the_idempotency_key_treats_null_actors_as_equal(probe):
    """System rows (DVP_SETTLEMENT) have no actor. A plain UNIQUE would let two of them share the
    key 'dvp-<order id>' (NULL is never equal to NULL), and one order could settle twice."""
    key = probe.one("unique_constraint_nulls", name="operations_actor_key_unique")
    assert key == {"definition": "UNIQUE NULLS NOT DISTINCT (actor_user_id, idempotency_key)",
                   "nulls_not_distinct": True}
    # The contrast: many orders are still waiting (settlement_operation_id NULL), so this one
    # MUST treat NULLs as different - it only stops one settlement event from settling two orders.
    settled_by = probe.one("unique_constraint_nulls", name="delivery_instructions_settlement_operation_id_key")
    assert settled_by == {"definition": "UNIQUE (settlement_operation_id)", "nulls_not_distinct": False}

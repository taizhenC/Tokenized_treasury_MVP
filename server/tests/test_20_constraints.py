"""Lessons 2-3: every constraint attack in tests/sql/constraints.sql is rejected, and nothing persists.

Each attack (tests/sql/constraints.sql) runs in its own transaction that is always rolled back.
"""

import psycopg
import pytest
from psycopg import errors as pg

from app import queries
from tests.helpers import CONSTRAINT_ATTACKS, PROBES, run

# block -> (error class, constraint name, or column name for NOT NULL)
EXPECTED = {
    "c01_negative_tokenized_balance": (pg.CheckViolation, "positions_tokenized_units_check"),
    "c01b_negative_traditional_balance": (pg.CheckViolation, "positions_traditional_units_check"),
    "c02_quantity_zero": (pg.CheckViolation, "operations_quantity_check"),
    "c02b_quantity_too_big": (pg.CheckViolation, "operations_quantity_check"),
    "c03_duplicate_actor_key": (pg.UniqueViolation, "operations_actor_key_unique"),
    "c04_representation_cash": (pg.CheckViolation, "ledger_entries_representation_check"),
    "c04b_representation_lowercase": (pg.CheckViolation, "ledger_entries_representation_check"),
    "c05_ledger_line_without_position": (pg.ForeignKeyViolation, "ledger_entries_position_fk"),
    "c05b_ledger_line_for_missing_position_asset": (pg.ForeignKeyViolation, "ledger_entries_position_fk"),
    "c05c_ledger_line_for_missing_operation": (pg.ForeignKeyViolation, "ledger_entries_operation_id_fkey"),
    "c05d_ledger_line_with_ids_swapped": (pg.ForeignKeyViolation, "ledger_entries_position_fk"),
    "c06_operation_type_mint": (pg.CheckViolation, "operations_columns_match_type"),
    "c07_self_transfer": (pg.CheckViolation, "operations_no_self_transfer"),
    "c08_transfer_without_actor": (pg.CheckViolation, "operations_columns_match_type"),
    "c08b_unknown_actor": (pg.ForeignKeyViolation, "operations_actor_user_id_fkey"),
    "c08c_unknown_destination": (pg.ForeignKeyViolation, "operations_destination_account_id_fkey"),
    "c08d_missing_idempotency_key": (pg.NotNullViolation, "idempotency_key"),
    "c08e_empty_idempotency_key": (pg.CheckViolation, "operations_idempotency_key_check"),
    "s1_conversion_with_destination": (pg.CheckViolation, "operations_columns_match_type"),
    "s2_transfer_without_accounts": (pg.CheckViolation, "operations_columns_match_type"),
    "s3_transfer_without_destination": (pg.CheckViolation, "operations_columns_match_type"),
    "s4_conversion_without_account": (pg.CheckViolation, "operations_columns_match_type"),
    "s5_opening_with_actor_and_source": (pg.CheckViolation, "operations_columns_match_type"),
    "s6_opening_credited_to_a_person": (pg.CheckViolation, "operations_columns_match_type"),
    "c09_zero_ledger_delta": (pg.CheckViolation, "ledger_entries_units_delta_check"),
    "c10_second_position_row": (pg.UniqueViolation, "positions_pkey"),
    "c10b_null_balance": (pg.NotNullViolation, "tokenized_units"),
    "c11_role_admin": (pg.CheckViolation, "users_role_check"),
    "c11b_participant_without_account": (pg.CheckViolation, "users_role_matches_account"),
    "c11c_auditor_with_account": (pg.CheckViolation, "users_role_matches_account"),
    "c11d_duplicate_username": (pg.UniqueViolation, "users_username_key"),
    "c11e_participant_linked_to_a_user_id": (pg.ForeignKeyViolation, "users_account_id_fkey"),
    "c12_unit_precision_two": (pg.CheckViolation, "assets_unit_precision_check"),
    "c12b_negative_custody": (pg.CheckViolation, "assets_total_custodied_units_check"),
    "c13_duplicate_wallet_ref": (pg.UniqueViolation, "accounts_wallet_ref_key"),
    "c15_delete_position_with_history": (pg.ForeignKeyViolation, "ledger_entries_position_fk"),
}


@pytest.fixture(scope="module")
def before(probe):
    snapshot = probe.snapshot()
    yield snapshot
    assert probe.snapshot() == snapshot, "a constraint attack left something behind"


def test_every_attack_is_listed():
    extra = {"l3_typed_identity_id", "info_new_account_not_approved_by_default"}
    assert set(CONSTRAINT_ATTACKS) == set(EXPECTED) | extra


@pytest.mark.parametrize("block", sorted(EXPECTED))
def test_attack_is_rejected(block, database_url, before):
    error_class, name = EXPECTED[block]
    with psycopg.connect(database_url) as conn:
        with pytest.raises(error_class) as caught:
            with conn.transaction():
                run(conn, CONSTRAINT_ATTACKS[block])
        diag = caught.value.diag
        assert name in (diag.constraint_name, diag.column_name)


def test_c03b_reserve_with_on_conflict_returns_no_row_instead_of_an_error(database_url, before):
    reserve = queries.load("convert_to_tokenized", queries.CONVERSION_BLOCKS)["reserve_idempotency_key"]
    params = {"actor_user_id": 11, "account_id": 101, "asset_id": 1, "quantity": 5,
              "idempotency_key": "neg-dup", "request_hash": "h"}
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            assert run(conn, reserve, params).fetchone() is not None
            assert run(conn, reserve, params).fetchone() is None


def test_c14_no_writes_inside_the_reconciliation_snapshot(database_url, before):
    set_snapshot = queries.load("reconciliation", queries.RECONCILIATION_BLOCKS)["set_snapshot"]
    with psycopg.connect(database_url) as conn:
        with pytest.raises(pg.ReadOnlySqlTransaction):
            with conn.transaction():
                run(conn, set_snapshot)
                run(conn, PROBES["touch_positions"])


def test_l3_identity_ids_cannot_be_typed_by_hand(database_url, before):
    with psycopg.connect(database_url) as conn:
        with pytest.raises(pg.GeneratedAlways):
            with conn.transaction():
                run(conn, CONSTRAINT_ATTACKS["l3_typed_identity_id"])


def test_info_a_new_account_is_not_approved_by_default(database_url, before):
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            row = run(conn, CONSTRAINT_ATTACKS["info_new_account_not_approved_by_default"]).fetchone()
    assert row == {"id": 109, "is_approved": False}

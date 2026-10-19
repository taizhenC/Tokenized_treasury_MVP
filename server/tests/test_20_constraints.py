"""Lessons 2-3 and 11: every constraint attack in tests/sql/constraints.sql is rejected, and nothing persists.

Each attack (tests/sql/constraints.sql) runs in its own transaction that is always rolled back.
Lesson 11 adds an attack on every rule of migration 004 (the cash leg), each built to break ONE
rule only, so the error can only name that rule.
"""

import psycopg
import pytest
from psycopg import errors as pg

from app import queries
from app.errors import classify_db_error
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
    # ---------- Lesson 11: the cash leg ----------
    "c16_operator_with_an_account": (pg.CheckViolation, "users_role_matches_account"),
    "c17_duplicate_settling_bank_id": (pg.UniqueViolation, "settling_banks_pkey"),
    "c17b_duplicate_settling_bank_name": (pg.UniqueViolation, "settling_banks_bank_name_key"),
    "c17c_account_settled_by_an_unknown_bank": (pg.ForeignKeyViolation, "accounts_settling_bank_id_fkey"),
    "c17d_delete_a_bank_that_still_settles_for_accounts": (pg.ForeignKeyViolation, "accounts_settling_bank_id_fkey"),
    "c18_second_settlement_day": (pg.UniqueViolation, "settlement_day_pkey"),
    "c18b_settlement_day_singleton_false": (pg.CheckViolation, "settlement_day_singleton_check"),
    "c18c_unknown_phase": (pg.CheckViolation, "settlement_day_phase_check"),
    "c18d_negative_maximum_net_debit_cap": (pg.CheckViolation, "settlement_day_max_net_debit_cap_cents_check"),
    "c18e_settlement_day_without_a_date": (pg.NotNullViolation, "business_date"),
    "c19_second_collateral_price_for_an_asset": (pg.UniqueViolation, "collateral_prices_pkey"),
    "c19b_collateral_price_for_an_unknown_asset": (pg.ForeignKeyViolation, "collateral_prices_asset_id_fkey"),
    "c19c_zero_prior_close": (pg.CheckViolation, "collateral_prices_prior_close_cents_check"),
    "c19d_haircut_below_2_percent": (pg.CheckViolation, "collateral_prices_haircut_bps_check"),
    "c19e_haircut_above_100_percent": (pg.CheckViolation, "collateral_prices_haircut_bps_check"),
    "c20_cap_inputs_for_an_unknown_account": (pg.ForeignKeyViolation, "net_debit_cap_inputs_account_id_fkey"),
    "c20b_second_cap_inputs_row": (pg.UniqueViolation, "net_debit_cap_inputs_pkey"),
    "c20c_negative_peak_average": (pg.CheckViolation, "net_debit_cap_inputs_avg_top3_peak_cents_check"),
    "c20d_factor_below_one": (pg.CheckViolation, "net_debit_cap_inputs_factor_bps_check"),
    "c20e_factor_above_two": (pg.CheckViolation, "net_debit_cap_inputs_factor_bps_check"),
    "c21_debit_beyond_the_net_debit_cap": (pg.CheckViolation, "settlement_within_net_debit_cap"),
    "c21b_collateral_monitor_below_zero": (pg.CheckViolation, "settlement_collateral_monitor_nonnegative"),
    "c21c_negative_collateral_value": (pg.CheckViolation, "settlement_accounts_collateral_value_cents_check"),
    "c21d_negative_net_debit_cap": (pg.CheckViolation, "settlement_accounts_net_debit_cap_cents_check"),
    "c21e_second_settlement_account": (pg.UniqueViolation, "settlement_accounts_pkey"),
    "c21f_settlement_account_for_an_unknown_account": (pg.ForeignKeyViolation, "settlement_accounts_account_id_fkey"),
    "c21g_null_net_settlement_balance": (pg.NotNullViolation, "net_cents"),
    "c21h_delta_cannot_take_a_one_cent_debit": (pg.CheckViolation, "settlement_within_net_debit_cap"),
    "c22_amount_zero": (pg.CheckViolation, "operations_amount_cents_check"),
    "c22b_amount_too_big": (pg.CheckViolation, "operations_amount_cents_check"),
    "c23_self_delivery_order": (pg.CheckViolation, "operations_no_self_transfer"),
    "c24_system_key_used_twice": (pg.UniqueViolation, "operations_actor_key_unique"),
    "s7_deliver_order_without_an_amount": (pg.CheckViolation, "operations_columns_match_type"),
    "s8_dvp_settlement_owned_by_a_person": (pg.CheckViolation, "operations_columns_match_type"),
    "s9_payment_that_moves_units": (pg.CheckViolation, "operations_columns_match_type"),
    "s10_payment_from_a_source_account": (pg.CheckViolation, "operations_columns_match_type"),
    "s11_valued_cutoff_naming_an_account": (pg.CheckViolation, "operations_columns_match_type"),
    "s12_final_settlement_without_an_actor": (pg.CheckViolation, "operations_columns_match_type"),
    "s13_transfer_with_an_amount": (pg.CheckViolation, "operations_columns_match_type"),
    "s14_opening_without_an_asset": (pg.CheckViolation, "operations_columns_match_type"),
    "s15_conversion_without_a_quantity": (pg.CheckViolation, "operations_columns_match_type"),
    "c26_instruction_for_an_unknown_order": (pg.ForeignKeyViolation,
                                             "delivery_instructions_submitted_operation_id_fkey"),
    "c26b_two_instructions_for_one_order": (pg.UniqueViolation, "delivery_instructions_submitted_operation_id_key"),
    "c26c_one_settlement_for_two_instructions": (pg.UniqueViolation,
                                                 "delivery_instructions_settlement_operation_id_key"),
    "c26d_settled_by_an_unknown_operation": (pg.ForeignKeyViolation,
                                             "delivery_instructions_settlement_operation_id_fkey"),
    "c26e_unknown_deliverer": (pg.ForeignKeyViolation, "delivery_instructions_deliverer_account_id_fkey"),
    "c26f_unknown_receiver": (pg.ForeignKeyViolation, "delivery_instructions_receiver_account_id_fkey"),
    "c26g_unknown_asset": (pg.ForeignKeyViolation, "delivery_instructions_asset_id_fkey"),
    "c27_instruction_quantity_zero": (pg.CheckViolation, "delivery_instructions_quantity_check"),
    "c27b_instruction_amount_zero": (pg.CheckViolation, "delivery_instructions_amount_cents_check"),
    "c27c_unknown_status": (pg.CheckViolation, "delivery_instructions_status_check"),
    "c27d_unknown_recycle_reason": (pg.CheckViolation, "delivery_instructions_recycle_reason_check"),
    "c27e_self_delivery_instruction": (pg.CheckViolation, "delivery_no_self_delivery"),
    "c28_settled_without_a_settlement_operation": (pg.CheckViolation, "delivery_settled_has_operation"),
    "c28b_recycling_with_a_settlement_operation": (pg.CheckViolation, "delivery_settled_has_operation"),
    "c28c_settled_with_a_recycle_reason": (pg.CheckViolation, "delivery_reason_matches_status"),
    "c28d_recycling_without_a_reason": (pg.CheckViolation, "delivery_reason_matches_status"),
    "c28e_dropped_without_a_reason": (pg.CheckViolation, "delivery_reason_matches_status"),
    "c29_cash_line_for_an_unknown_operation": (pg.ForeignKeyViolation, "cash_ledger_entries_operation_id_fkey"),
    "c29b_cash_line_for_an_unknown_settlement_account": (pg.ForeignKeyViolation,
                                                         "cash_ledger_entries_account_id_fkey"),
    "c29c_zero_cash_line": (pg.CheckViolation, "cash_ledger_entries_amount_cents_check"),
    "c29d_unknown_cash_ledger": (pg.CheckViolation, "cash_ledger_entries_ledger_check"),
    "c29e_participant_line_without_an_account": (pg.CheckViolation, "cash_line_shape"),
    "c29f_fed_line_naming_an_account": (pg.CheckViolation, "cash_line_shape"),
    "c29g_cash_line_without_a_business_date": (pg.NotNullViolation, "business_date"),
    "c29h_participant_line_naming_a_bank": (pg.CheckViolation, "cash_line_bank_only_on_fed"),
    "c29i_fed_line_for_an_unknown_bank": (pg.ForeignKeyViolation, "cash_ledger_entries_settling_bank_id_fkey"),
    "c30_a_day_closes_only_once": (pg.UniqueViolation, "settlement_figures_pkey"),
    "c30b_figure_for_an_unknown_settlement_account": (pg.ForeignKeyViolation, "settlement_figures_account_id_fkey"),
    "c30c_two_nss_lines_for_one_bank": (pg.UniqueViolation, "nss_file_lines_pkey"),
    "c30d_nss_line_for_an_unknown_bank": (pg.ForeignKeyViolation, "nss_file_lines_settling_bank_id_fkey"),
}

# The tables migration 004 created, and the rules it added or re-created on older tables.
LESSON_11_TABLES = {
    "settling_banks", "settlement_day", "collateral_prices", "net_debit_cap_inputs", "settlement_accounts",
    "delivery_instructions", "cash_ledger_entries", "settlement_figures", "nss_file_lines",
}
LESSON_11_RULES_ON_OLDER_TABLES = {
    "accounts_settling_bank_id_fkey", "operations_amount_cents_check", "users_role_check",
    "users_role_matches_account", "operations_columns_match_type", "operations_actor_key_unique",
    "operations_operation_type_check",
}
# Rules no single statement can be the FIRST to break:
NOT_ATTACKED = {
    # identity ids are generated, never typed (see l3_typed_identity_id), so two rows never share one
    "delivery_instructions_pkey", "cash_ledger_entries_pkey",
    # a type outside the list also matches no shape, and Postgres checks CHECKs in name order:
    # operations_columns_match_type always reports first (see c06_operation_type_mint)
    "operations_operation_type_check",
}


@pytest.fixture(scope="module")
def before(probe):
    snapshot = probe.snapshot()
    yield snapshot
    assert probe.snapshot() == snapshot, "a constraint attack left something behind"


def test_every_attack_is_listed():
    extra = {
        "l3_typed_identity_id", "info_new_account_not_approved_by_default",
        "l11_typed_generated_column", "info_generated_column_follows_the_haircut",
        "info_amount_at_the_upper_edge_is_accepted",
    }
    assert set(CONSTRAINT_ATTACKS) == set(EXPECTED) | extra


def test_every_lesson_11_rule_is_attacked(probe):
    rules = {
        r["conname"] for r in probe.rows("constraints")
        if r["table_name"] in LESSON_11_TABLES or r["conname"] in LESSON_11_RULES_ON_OLDER_TABLES
    }
    attacked = {name for _, name in EXPECTED.values()}
    assert rules - NOT_ATTACKED - attacked == set()
    assert NOT_ATTACKED <= rules


@pytest.mark.parametrize("block", sorted(EXPECTED))
def test_attack_is_rejected(block, database_url, before):
    error_class, name = EXPECTED[block]
    with psycopg.connect(database_url) as conn:
        with pytest.raises(error_class) as caught:
            with conn.transaction():
                run(conn, CONSTRAINT_ATTACKS[block])
        diag = caught.value.diag
        assert name in (diag.constraint_name, diag.column_name)


@pytest.mark.parametrize("block,message", [
    ("c21_debit_beyond_the_net_debit_cap", "net debit cap exceeded"),
    ("c21h_delta_cannot_take_a_one_cent_debit", "net debit cap exceeded"),
    ("c21b_collateral_monitor_below_zero", "collateral monitor would go negative"),
])
def test_a_risk_control_backstop_answers_with_its_own_message(block, message, database_url, before):
    """The app checks both controls first (and recycles); if a bug ever skipped that check, the
    CHECK still stops the breach, and the 422 says which control held."""
    with psycopg.connect(database_url) as conn:
        with pytest.raises(pg.CheckViolation) as caught:
            with conn.transaction():
                run(conn, CONSTRAINT_ATTACKS[block])
    status, body = classify_db_error(caught.value)
    assert (status, body["error"], body["pgCode"]) == (422, message, "23514")
    assert body["constraint"] == EXPECTED[block][1]


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


def test_l11_the_collateral_per_unit_cannot_be_typed_by_hand(database_url, before):
    with psycopg.connect(database_url) as conn:
        with pytest.raises(pg.GeneratedAlways):
            with conn.transaction():
                run(conn, CONSTRAINT_ATTACKS["l11_typed_generated_column"])


def test_info_the_largest_amount_is_accepted(database_url, before):
    """The upper edge of operations_amount_cents_check: $100 billion passes, one cent more (c22b) does not."""
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            row = run(conn, CONSTRAINT_ATTACKS["info_amount_at_the_upper_edge_is_accepted"]).fetchone()
    assert row == {"amount_cents": 10_000_000_000_000}


def test_info_the_collateral_per_unit_follows_the_haircut(database_url, before):
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            row = run(conn, CONSTRAINT_ATTACKS["info_generated_column_follows_the_haircut"]).fetchone()
    assert row == {"collateral_cents_per_unit": 9000}

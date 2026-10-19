"""Lessons 4 and 11: the seed data, the dev password hash, and why a seed must never run twice.

Lesson 11's seed (004_settlement_reference.sql) adds the operator login and the cash leg's
reference data; its numbers are cross-checked with the formulas in app/domain/risk.py.
"""

import re

import bcrypt
import psycopg
import pytest

from app.domain import risk
from tests.helpers import PASSWORD, SEEDED_POSITIONS
from tests.safety import SERVER_DIR

SEEDS = SERVER_DIR / "db" / "seeds"


def test_the_bcryptjs_hash_in_the_seed_verifies_with_python_bcrypt(probe):
    hashes = set()
    for seed in ("002_users.sql", "004_settlement_reference.sql"):  # the operator reuses the same dev hash
        hashes |= set(re.findall(r"\$2b\$10\$[^']+", (SEEDS / seed).read_text(encoding="utf-8-sig")))
    assert len(hashes) == 1
    seed_hash = hashes.pop().encode()
    assert bcrypt.checkpw(PASSWORD.encode(), seed_hash)
    assert not bcrypt.checkpw(b"wrong", seed_hash)
    assert [r["password_hash"] for r in probe.rows("password_hashes")] == [seed_hash.decode()]


def test_accounts_and_users(probe):
    assert [(r["id"], r["is_approved"]) for r in probe.rows("accounts")] == [
        (101, True), (102, True), (103, True), (104, False),
    ]
    assert [(r["id"], r["username"], r["role"], r["account_id"]) for r in probe.rows("users")] == [
        (11, "alpha", "PARTICIPANT", 101),
        (12, "beta", "PARTICIPANT", 102),
        (13, "gamma", "PARTICIPANT", 103),
        (14, "auditor", "AUDITOR", None),
        (15, "operator", "OPERATOR", None),  # Lesson 11: the settlement desk holds no account
    ]


def test_opening_balances_are_ledger_events(probe):
    assert probe.positions() == SEEDED_POSITIONS
    assert probe.value("held_total") == 3000
    assert [
        (r["operation_type"], r["actor_user_id"], r["source_account_id"], r["destination_account_id"],
         r["quantity"], r["idempotency_key"], r["amount_cents"])
        for r in probe.rows("operations")
    ] == [
        ("OPENING", None, None, 101, 1000, "opening-account-101", None),
        ("OPENING", None, None, 102, 1000, "opening-account-102", None),
        ("OPENING", None, None, 103, 1000, "opening-account-103", None),
    ]
    assert [(r["account_id"], r["representation"], r["units_delta"]) for r in probe.rows("ledger_entries")] == [
        (101, "TRADITIONAL", 1000), (102, "TRADITIONAL", 1000), (103, "TRADITIONAL", 1000),
    ]
    assert [(r["account_id"], r["total"]) for r in probe.rows("ledger_sum_per_account")] == [
        (101, 1000), (102, 1000), (103, 1000),
    ]


def test_running_seed_003_again_in_one_transaction_fails_and_changes_nothing(probe, database_url):
    before = probe.snapshot()
    with psycopg.connect(database_url) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation) as caught:
            with conn.transaction():  # like psql -1: the whole file, or nothing
                conn.execute((SEEDS / "003_opening_balances.sql").read_text(encoding="utf-8-sig"))
    assert caught.value.diag.constraint_name == "positions_pkey"
    assert probe.snapshot() == before


# ---------- Lesson 11: the settlement reference data ----------

def test_settling_banks(probe):
    assert [(r["account_id"], r["settling_bank_id"], r["bank_name"]) for r in probe.rows("settling_banks")] == [
        (101, 201, "Settling Bank One (demo)"),
        (102, 201, "Settling Bank One (demo)"),
        (103, 202, "Settling Bank Two (demo)"),
        (104, 202, "Settling Bank Two (demo)"),
    ]


def test_settlement_day_opens_on_the_seed_day(probe):
    """The seed opens the first weekday on or after the day it ran (compared with the date the
    runner recorded for seed 004, so it does not matter on which day the tests run)."""
    day = probe.one("settlement_day")
    assert (day["phase"], day["max_net_debit_cap_cents"], day["is_seed_day"]) == ("OPEN", 3_844_000, True)
    assert day["business_date"].weekday() < 5  # Monday 0 ... Friday 4
    # DTC's maximum net debit cap, $3.844B, scaled down by 100,000 like every dollar figure here.
    assert day["max_net_debit_cap_cents"] * 100_000 == 3_844_000_000 * 100


def test_collateral_price_and_its_generated_value(probe):
    assert probe.rows("collateral_prices") == [
        {"asset_id": 1, "prior_close_cents": 10_000, "haircut_bps": 500, "collateral_cents_per_unit": 9_500},
    ]
    assert risk.collateral_cents_per_unit(10_000, 500) == 9_500  # Postgres and the app agree


def test_settlement_accounts_start_flat_with_computed_collateral_and_caps(probe):
    per_unit = probe.one("collateral_prices")["collateral_cents_per_unit"]
    maximum = probe.one("settlement_day")["max_net_debit_cap_cents"]
    inputs = {r["account_id"]: r for r in probe.rows("net_debit_cap_inputs")}
    assert [(a, r["avg_top3_peak_cents"], r["factor_bps"]) for a, r in inputs.items()] == [
        (101, 3_200_000, 12_500), (102, 1_000_000, 15_000), (103, 2_000_000, 12_500), (104, 0, 10_000),
    ]
    accounts = probe.rows("settlement_accounts")
    assert [(r["account_id"], r["net_cents"], r["collateral_value_cents"], r["net_debit_cap_cents"])
            for r in accounts] == [
        (101, 0, 9_500_000, 3_844_000),  # $40,000 capped at the maximum
        (102, 0, 9_500_000, 1_500_000),
        (103, 0, 9_500_000, 2_500_000),
        (104, 0, 0, 0),                  # Delta: no units; a $0 cap (a lab choice)
    ]
    traditional = {r["account_id"]: r["traditional_units"] for r in probe.rows("positions")}
    for row in accounts:
        cap_inputs = inputs[row["account_id"]]
        assert row["net_debit_cap_cents"] == risk.net_debit_cap(
            cap_inputs["avg_top3_peak_cents"], cap_inputs["factor_bps"], maximum)
        assert row["collateral_value_cents"] == traditional[row["account_id"]] * per_unit


def test_running_seed_004_again_in_one_transaction_fails_and_changes_nothing(probe, database_url):
    before = probe.snapshot()
    with psycopg.connect(database_url) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation) as caught:
            with conn.transaction():
                conn.execute((SEEDS / "004_settlement_reference.sql").read_text(encoding="utf-8-sig"))
    assert caught.value.diag.constraint_name == "settling_banks_pkey"  # its first statement
    assert probe.snapshot() == before

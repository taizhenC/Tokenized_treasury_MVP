"""Lesson 4: the seed data, the dev password hash, and why a seed must never run twice."""

import re

import bcrypt
import psycopg
import pytest

from tests.helpers import PASSWORD, SEEDED_POSITIONS
from tests.safety import SERVER_DIR

SEEDS = SERVER_DIR / "db" / "seeds"


def test_the_bcryptjs_hash_in_the_seed_verifies_with_python_bcrypt(probe):
    hashes = set(re.findall(r"\$2b\$10\$[^']+", (SEEDS / "002_users.sql").read_text(encoding="utf-8-sig")))
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
    ]


def test_opening_balances_are_ledger_events(probe):
    assert probe.positions() == SEEDED_POSITIONS
    assert probe.value("held_total") == 3000
    assert [
        (r["operation_type"], r["actor_user_id"], r["source_account_id"], r["destination_account_id"],
         r["quantity"], r["idempotency_key"])
        for r in probe.rows("operations")
    ] == [
        ("OPENING", None, None, 101, 1000, "opening-account-101"),
        ("OPENING", None, None, 102, 1000, "opening-account-102"),
        ("OPENING", None, None, 103, 1000, "opening-account-103"),
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

"""Lesson 11: migration 004 and its seed on a database that is ALREADY IN USE (the deployed one).

On Render, accounts, positions and operations already exist (and have changed) when 004 arrives.
So the migration must accept every existing row, and the seed must compute each account's
starting collateral from the positions it finds at that moment - not the fresh-seed 1,000 units.

Everything here runs on a scratch database, <test database>_upgrade, that this module creates and
always drops again (from the maintenance database, like tests/reset_db.py). The steps share it and
run in file order:
  1. the runner with a copy of db/ that holds only files 001-003: a database from before Lesson 11
  2. balances change the way Lessons 6-7 changed them (each with its operation and ledger lines)
  3. the REAL runner with --seed: exactly the two 004 files apply, and collateral follows the
     CURRENT positions (tokenized units count zero)
"""

import os
import shutil
import subprocess
import sys

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.domain import reconciliation, risk, transfers
from db import migrate
from tests.helpers import Probe
from tests.reset_db import ADMIN
from tests.safety import SERVER_DIR

BEFORE_LESSON_11 = [
    "migrations/001_create_accounts_and_assets.sql",
    "migrations/002_create_users_and_positions.sql",
    "migrations/003_create_operations_and_ledger_entries.sql",
    "seeds/001_reference_data.sql",
    "seeds/002_users.sql",
    "seeds/003_opening_balances.sql",
]
LESSON_11 = ["migrations/004_add_settlement_risk_controls.sql", "seeds/004_settlement_reference.sql"]
# After step 2: alpha minted 100 and sent 40 of them to beta; beta minted 300.
POSITIONS_BEFORE_004 = "101:900/60 102:700/340 103:1000/0 104:0/0"


class Scratch:
    def __init__(self, url: str) -> None:
        self.url = url
        self.conn = psycopg.connect(url, autocommit=True)
        self.probe = Probe(self.conn)


@pytest.fixture(scope="module")
def scratch(database_url):
    name = conninfo_to_dict(database_url)["dbname"] + "_upgrade"
    maintenance = make_conninfo(database_url, dbname="postgres")

    def admin(block: str) -> None:
        with psycopg.connect(maintenance, autocommit=True) as conn:
            conn.execute(sql.SQL(ADMIN[block]).format(database=sql.Identifier(name)))

    admin("drop_database")  # left over from an interrupted run, if any
    admin("create_database")
    scratch = None
    try:
        scratch = Scratch(make_conninfo(database_url, dbname=name))
        yield scratch
    finally:
        if scratch is not None:
            scratch.conn.close()
        admin("drop_database")  # always, even when a step failed (WITH FORCE ends any leftover session)


def run_real_runner(url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url}  # the runner reads DATABASE_URL; .env never overrides it
    return subprocess.run([sys.executable, "-m", "db.migrate", "--seed"],
                          cwd=SERVER_DIR, env=env, capture_output=True, text=True)


def test_step1_a_database_from_before_lesson_11(scratch, tmp_path, monkeypatch, capsys):
    for name in BEFORE_LESSON_11:
        (tmp_path / name).parent.mkdir(exist_ok=True)
        shutil.copy(SERVER_DIR / "db" / name, tmp_path / name)
    monkeypatch.setattr(migrate, "DB_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["migrate", "--seed"])
    monkeypatch.setenv("DATABASE_URL", scratch.url)
    migrate.main()
    assert capsys.readouterr().out.splitlines() == [f"applied {name}" for name in BEFORE_LESSON_11]
    assert [r["filename"] for r in scratch.probe.rows("schema_migrations")] == BEFORE_LESSON_11
    assert scratch.probe.value("table_exists", name="public.settlement_accounts") is False
    assert scratch.probe.positions() == "101:1000/0 102:1000/0 103:1000/0 104:0/0"


def test_step2_balances_change_before_the_upgrade(scratch):
    probe = scratch.probe
    # A mint the old way: operation + position + two ledger lines (rowcount = the two lines).
    assert probe.execute("mint_before_lesson_11", actor_user_id=11, account_id=101, quantity=100,
                         idempotency_key="old-mint-a") == 2
    assert probe.execute("mint_before_lesson_11", actor_user_id=12, account_id=102, quantity=300,
                         idempotency_key="old-mint-b") == 2
    # Lesson 7's transfer is unchanged by Lesson 11, so the app's own code runs it on the old schema.
    with psycopg.connect(scratch.url) as conn:
        outcome = transfers.transfer(conn, actor_user_id=11, source_account_id=101, destination_account_id=102,
                                     asset_id=1, quantity=40, idempotency_key="old-transfer")
    assert outcome.status == 201, outcome.body
    assert probe.positions() == POSITIONS_BEFORE_004
    assert all(r["net"] == 0 for r in probe.rows("ledger_net_per_operation") if r["operation_type"] != "OPENING")


def test_step3_the_real_runner_applies_exactly_the_two_004_files(scratch):
    done = run_real_runner(scratch.url)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [f"applied {name}" for name in LESSON_11]
    assert [r["filename"] for r in scratch.probe.rows("schema_migrations")] == sorted(BEFORE_LESSON_11 + LESSON_11)
    again = run_real_runner(scratch.url)
    assert again.returncode == 0, again.stderr
    assert again.stdout.strip() == "Database is up to date."


def test_collateral_follows_the_current_positions_not_the_fresh_seed(scratch):
    probe = scratch.probe
    assert probe.positions() == POSITIONS_BEFORE_004  # the upgrade moved no units
    price = probe.one("collateral_prices")
    per_unit = risk.collateral_cents_per_unit(price["prior_close_cents"], price["haircut_bps"])
    assert per_unit == price["collateral_cents_per_unit"] == 9500
    traditional = {r["account_id"]: r["traditional_units"] for r in probe.rows("positions")}
    accounts = probe.rows("settlement_accounts")
    assert [(r["account_id"], r["net_cents"], r["collateral_value_cents"]) for r in accounts] == [
        (account_id, 0, units * per_unit) for account_id, units in traditional.items()
    ]
    # In numbers: alpha 900 x 9,500 (its 60 tokenized units count zero), beta 700 x 9,500.
    assert [r["collateral_value_cents"] for r in accounts] == [8_550_000, 6_650_000, 9_500_000, 0]
    assert [r["net_debit_cap_cents"] for r in accounts] == [3_844_000, 1_500_000, 2_500_000, 0]


def test_the_existing_rows_survive_the_new_rules(scratch):
    """Every ALTER ... ADD CONSTRAINT in 004 checked every existing row: the old operations got
    amount_cents NULL and still match their shape, and the old users still match their roles."""
    probe = scratch.probe
    assert [(r["operation_type"], r["quantity"], r["amount_cents"]) for r in probe.rows("operations")] == [
        ("OPENING", 1000, None), ("OPENING", 1000, None), ("OPENING", 1000, None),
        ("CONVERT_TO_TOKENIZED", 100, None), ("CONVERT_TO_TOKENIZED", 300, None), ("TRANSFER", 40, None),
    ]
    assert [(r["username"], r["role"]) for r in probe.rows("users")] == [
        ("alpha", "PARTICIPANT"), ("beta", "PARTICIPANT"), ("gamma", "PARTICIPANT"), ("auditor", "AUDITOR"),
        ("operator", "OPERATOR"),
    ]
    assert [r["settling_bank_id"] for r in probe.rows("settling_banks")] == [201, 201, 202, 202]


def test_the_upgraded_database_reconciles(scratch):
    with psycopg.connect(scratch.url) as conn:
        report = reconciliation.run_report(conn)
    assert report["passed"] is True, report
    assert all(r["ok"] is True for r in report["collateralVsPositions"])

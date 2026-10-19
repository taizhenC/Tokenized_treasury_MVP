"""Where the SQL meets the driver: the psycopg 3 rules the app relies on, each checked for real.

Rule                                                    test
%(name)s named parameters only (never $1 or %s)         named_placeholders_only
one "-- name:" block = exactly one statement            exactly_one_statement
transactions come from `with conn.transaction():`       raw_commit_breaks_atomicity
-%(quantity)s works because ints are sent typed         unary_minus
SUM/COUNT arrive as exact Python ints                   aggregates
SUM of a BIGINT (money) needs ::bigint, else Decimal    money_sums_need_a_bigint_cast
store_result needs psycopg.types.json.Jsonb             store_result_needs_jsonb
a stored result holds JSON values only (no date/Decimal) stored_results_refuse_dates_and_decimals
find locked rows by account id, never by position       rows_come_back_in_account_id_order
the engine locks every row in one global order          engine_locks_in_one_global_order
a killed connection is an exception, not a crash        (test_70: server survives a kill)
"""

import os
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors as pg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout

from app import queries
from app.domain import reconciliation, settlement_engine
from app.domain.transactions import transaction
from app.errors import classify_db_error
from app.sql import load_sql_blocks
from tests.helpers import PROBES, new_key, run, set_tokenized
from tests.safety import SERVER_DIR
from tests.spy import SpyConnection, SpyPool

ALL_BLOCKS = {
    f"{stem}.{name}": sql
    for stem, required in queries.ALL_FILES.items()
    for name, sql in queries.load(stem, required).items()
}
ALL_BLOCKS.update(
    {f"runner-bookkeeping.{name}": sql
     for name, sql in load_sql_blocks(SERVER_DIR / "db" / "runner-bookkeeping.sql").items()}
)
TRANSFER = queries.load("transfer", queries.TRANSFER_BLOCKS)
MINT = queries.load("convert_to_tokenized", queries.CONVERSION_BLOCKS)
RECON = queries.load("reconciliation", queries.RECONCILIATION_BLOCKS)
ENGINE = queries.load("settlement_engine", queries.SETTLEMENT_ENGINE_BLOCKS)


def test_there_are_93_blocks():
    # The 13 query files hold 90 (31 before Lessons 11-14), plus the runner's 3.
    assert sum(len(blocks) for blocks in queries.ALL_FILES.values()) == 90
    assert len(ALL_BLOCKS) == 93


@pytest.mark.parametrize("stem", sorted(queries.ALL_FILES))
def test_each_query_file_holds_exactly_the_blocks_the_app_needs(stem):
    """No forgotten experiments: every block in a file is one the app runs (app/queries.py)."""
    assert set(queries.load(stem, queries.ALL_FILES[stem])) == set(queries.ALL_FILES[stem])


# Blocks the app (or the runner) runs WITHOUT a parameter dict. psycopg does no %-parsing at all
# for them, so their SQL is sent exactly as written: a literal % is written as one %.
RUN_WITHOUT_PARAMETERS = {
    "runner-bookkeeping.create_schema_migrations",
    "runner-bookkeeping.list_applied_files",
    "holdings.holdings_all",
    "holdings.list_accounts",
    "history.history_all",
    "reconciliation.set_snapshot",
    "reconciliation.check_conservation",
    "reconciliation.check_positions_vs_ledger",
    # Lessons 12-14
    "reconciliation.check_cash_lines_net_to_zero",
    "reconciliation.check_settlement_vs_cash_ledger",
    "reconciliation.check_collateral_vs_positions",
    "reconciliation.check_settled_instructions_vs_ledgers",
    "reconciliation.check_risk_controls",
    "reconciliation.check_closed_days",
    "settlement_engine.lock_settlement_day",
    "settlement_engine.lock_settlement_accounts",
    "valued_cutoff.lock_settlement_day",
    "final_settlement.lock_settlement_day",
    "final_settlement.lock_settlement_accounts",
    "settlement_reads.delivery_instructions_all",
    "settlement_reads.closed_day_figures",
    "settlement_reads.closed_day_nss_lines",
}


def test_the_no_parameter_list_names_real_blocks():
    assert RUN_WITHOUT_PARAMETERS <= set(ALL_BLOCKS)


@pytest.mark.parametrize("name,stems", [
    ("find_existing_operation", ["convert_to_tokenized", "convert_to_traditional", "transfer", "deliver_order",
                                 "settlement_payment", "valued_cutoff", "final_settlement"]),
    ("store_result", ["convert_to_tokenized", "convert_to_traditional", "transfer", "deliver_order",
                      "settlement_payment", "valued_cutoff", "final_settlement"]),
    ("lock_settlement_day", ["settlement_engine", "valued_cutoff", "final_settlement"]),
    ("lock_settlement_accounts", ["settlement_engine", "final_settlement"]),
])
def test_a_statement_shared_by_several_flows_is_the_same_text_everywhere(name, stems):
    """One sequencer lock, one way to lock the settlement accounts: every flow takes the same rows
    with the same statement (comments may differ; app.sql drops them)."""
    assert len({ALL_BLOCKS[f"{stem}.{name}"] for stem in stems}) == 1


@pytest.mark.parametrize("name", sorted(ALL_BLOCKS))
def test_named_placeholders_only(name):
    sql = ALL_BLOCKS[name]
    assert not re.search(r"\$\d", sql), "positional $n placeholder: write %(name)s"
    if name in RUN_WITHOUT_PARAMETERS:
        # Sent as-is: a placeholder would reach Postgres as text, and %% would stay %%.
        assert "%(" not in sql, "the app runs this block without parameters: no %(name)s here"
        assert "%%" not in sql, "this block runs without parameters, so write a literal % once, not %%"
        return
    # Runs WITH a parameter dict: psycopg reads every % as the start of a placeholder.
    leftover = re.sub(r"%\(\w+\)s", "", sql).replace("%%", "")
    assert "%" not in leftover, "this block gets parameters: write a literal % as %% (e.g. LIKE 'SIM-%%')"


@pytest.mark.parametrize("name", sorted(ALL_BLOCKS))
def test_exactly_one_statement_and_no_transaction_control(name):
    code = re.sub(r"--[^\n]*", "", ALL_BLOCKS[name]).strip()
    assert code.count(";") == 1 and code.endswith(";")
    assert code.split()[0].upper() not in {"BEGIN", "COMMIT", "ROLLBACK", "START"}


def test_unary_minus_needs_no_cast_when_the_parameter_is_an_int(database_url, probe):
    op_id = probe.value("any_operation_id")  # any existing operation will do for the foreign key
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            params = {"operation_id": op_id, "account_id": 101, "asset_id": 1, "quantity": 5}
            assert run(conn, MINT["insert_ledger_entries"], params).rowcount == 2
            lines = run(conn, PROBES["ledger_lines_for_operation"], {"operation_id": op_id}).fetchall()
            assert [line["units_delta"] for line in lines][-2:] == [-5, 5]
        # A value sent as TEXT is an untyped parameter: Postgres cannot choose the operator (42725).
        with pytest.raises(pg.AmbiguousFunction):
            with conn.transaction():
                run(conn, MINT["insert_ledger_entries"], {**params, "quantity": "5"})


def test_aggregates_are_exact_python_ints(probe):
    row = probe.one("types_of_aggregates")
    assert type(row["n"]) is int and type(row["s"]) is int and type(row["si"]) is int
    assert type(row["a"]) is Decimal  # NUMERIC arrives as Decimal (never float)


def test_money_sums_need_a_bigint_cast(probe):
    """SUM of an INTEGER column is a BIGINT (an int), but SUM of a BIGINT column - every money
    column - is a NUMERIC, so it arrives as Decimal: Jsonb cannot store it and FastAPI would send a
    float. Every money SUM in the query files is therefore written SUM(...)::bigint."""
    row = probe.one("types_of_money_values")
    assert type(row["s"]) is Decimal
    assert type(row["sb"]) is int and row["sb"] == row["s"]
    assert type(row["d"]) is date  # a DATE column arrives as datetime.date


def test_stored_results_refuse_dates_and_decimals(database_url, probe):
    """A POST result is stored with Jsonb and replayed later, so it may hold JSON values only:
    a business date goes in as date.isoformat() text, a sum as an int."""
    store = TRANSFER["store_result"]
    op_id = probe.value("any_operation_id")
    with psycopg.connect(database_url) as conn:
        for value in (date(2026, 10, 19), Decimal("7844000")):
            with pytest.raises(TypeError, match="is not JSON serializable"):
                with conn.transaction():
                    run(conn, store, {"operation_id": op_id, "result": Jsonb({"value": value})})
        with conn.transaction(force_rollback=True):
            result = Jsonb({"businessDate": date(2026, 10, 19).isoformat(), "netCents": int(Decimal("7844000"))})
            assert run(conn, store, {"operation_id": op_id, "result": result}).rowcount == 1


def test_store_result_needs_jsonb(database_url, probe):
    store = TRANSFER["store_result"]
    op_id = probe.value("any_operation_id")
    with psycopg.connect(database_url) as conn:
        with pytest.raises(psycopg.ProgrammingError, match="cannot adapt type 'dict'"):
            with conn.transaction():
                run(conn, store, {"operation_id": op_id, "result": {"a": 1}})
        with pytest.raises(psycopg.Error):  # a list is sent as a Postgres ARRAY, not JSON
            with conn.transaction():
                run(conn, store, {"operation_id": op_id, "result": [{"a": 1}]})
        with conn.transaction(force_rollback=True):
            assert run(conn, store, {"operation_id": op_id, "result": Jsonb([{"a": 1}])}).rowcount == 1


def test_rows_come_back_in_account_id_order_not_source_first(database_url):
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            rows = run(conn, TRANSFER["lock_positions"],
                       {"source_account_id": 103, "destination_account_id": 101, "asset_id": 1}).fetchall()
    assert [r["account_id"] for r in rows] == [101, 103]  # so the app looks the source up by id


def test_engine_locks_in_one_global_order(database_url):
    """The settlement engine's lock order: the sequencer row, then EVERY position of the asset,
    then EVERY settlement account, each list lowest account_id first. Every flow takes its rows
    in this one order (or a subset of it), so no two flows can wait for each other in a circle.
    The real settlement_engine.lock() runs on a spy cursor, which records each statement it sends
    (test_92 shows the rows are really locked in that order, with requests queued behind them)."""
    spy = SpyPool(None)
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            engine = settlement_engine.lock(SpyConnection(conn, spy).cursor(row_factory=dict_row), 1)
            # Each statement on its own: its rows come back (and are locked) lowest account_id first.
            day = run(conn, ENGINE["lock_settlement_day"]).fetchall()
            positions = run(conn, ENGINE["lock_engine_positions"], {"asset_id": 1}).fetchall()
            accounts = run(conn, ENGINE["lock_settlement_accounts"]).fetchall()
    assert spy.seen == ["lock_settlement_day", "lock_engine_positions", "lock_settlement_accounts",
                        "read_collateral_price"]
    assert sorted(engine.accounts) == [101, 102, 103, 104]
    assert len(day) == 1  # the singleton row
    assert [r["account_id"] for r in positions] == [101, 102, 103, 104]
    assert [r["account_id"] for r in accounts] == [101, 102, 103, 104]


def test_the_source_row_is_found_by_account_id_not_by_position(users, probe):
    """The rows[0] trap, through the API: with rows[0] the first case is a FALSE 422
    (alpha's 10 < 11) and the second slips past the app check (alpha's balance is enough)."""
    set_tokenized(users, probe, 101, 10)
    set_tokenized(users, probe, 102, 50)
    response = users["beta"].transfer(101, 11, new_key("rows0"))
    assert response.status_code == 201
    set_tokenized(users, probe, 103, 5)
    response = users["gamma"].transfer(101, 6, new_key("rows0"))
    assert response.status_code == 422
    assert response.json() == {"error": "insufficient tokenized units"}  # the app check, not the CHECK backstop


@pytest.mark.parametrize(
    "error,status",
    [(pg.ForeignKeyViolation("x"), 422), (pg.CheckViolation("x"), 422), (pg.UniqueViolation("x"), 409),
     (pg.AdminShutdown("x"), 503), (psycopg.OperationalError("the connection is lost"), 503),
     (PoolTimeout("x"), 503), (pg.DeadlockDetected("x"), 503), (pg.SerializationFailure("x"), 503),
     (pg.IdleInTransactionSessionTimeout("x"), 503), (pg.DataError("x"), 500), (pg.SyntaxError("x"), 500)],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_error_classes_map_to_http_codes(error, status):
    assert classify_db_error(error)[0] == status


def explain(conn, params):
    # The app's lock statement, prefixed with EXPLAIN by the test.
    return [r["QUERY PLAN"] for r in run(conn, "EXPLAIN (COSTS OFF) " + TRANSFER["lock_positions"], params)]


def test_lock_plan_sorts_below_the_row_lock(database_url, probe):
    params = {"source_account_id": 103, "destination_account_id": 101, "asset_id": 1}
    probe.execute("analyze_positions")  # what autovacuum does on its own
    with psycopg.connect(database_url) as conn:
        plan = explain(conn, params)
        assert plan[0] == "LockRows"
        assert any("Sort Key: account_id" in line for line in plan) or any("positions_pkey" in line for line in plan)
        run(conn, PROBES["disable_index_scan"])
        run(conn, PROBES["disable_bitmap_scan"])
        plan = explain(conn, params)
        assert plan[0] == "LockRows" and plan[1].strip() == "->  Sort"
        assert plan[2].strip() == "Sort Key: account_id"
        conn.rollback()


def test_transaction_helper_refuses_a_connection_that_already_ran_a_statement(database_url):
    with psycopg.connect(database_url) as conn:
        run(conn, PROBES["no_op"])  # psycopg opened a transaction implicitly
        with pytest.raises(RuntimeError, match="needs an idle connection"):
            with transaction(conn):
                pass
        conn.rollback()


def test_a_raw_commit_inside_a_psycopg_transaction_breaks_atomicity(database_url):
    """Why the query files lost their begin/commit/rollback blocks."""
    with psycopg.connect(database_url) as conn:
        run(conn, PROBES["create_scratch_table"])
        conn.commit()
        with pytest.raises(RuntimeError):
            with conn.transaction():
                run(conn, PROBES["insert_scratch"], {"x": 1})
                run(conn, PROBES["raw_commit"])  # ends the transaction behind psycopg's back
                run(conn, PROBES["insert_scratch"], {"x": 2})
                raise RuntimeError("fail after the raw COMMIT")
        assert run(conn, PROBES["count_scratch"]).fetchone()["n"] == 1  # the first half survived


def test_set_snapshot_lasts_for_one_transaction_only(database_url):
    with psycopg.connect(database_url) as conn:
        report = reconciliation.run_report(conn)
        assert report["passed"] is True
        settings = run(conn, PROBES["transaction_settings"]).fetchone()
        assert settings == {"isolation": "read committed", "read_only": "off"}  # nothing leaks to the pool
        conn.rollback()


def test_set_snapshot_must_be_the_first_statement(database_url):
    with psycopg.connect(database_url) as conn:
        with pytest.raises(pg.ActiveSqlTransaction):
            with conn.transaction():
                run(conn, PROBES["no_op"])
                run(conn, RECON["set_snapshot"])


def test_try_block_helper_runs_a_block_and_rolls_back(database_url, probe):
    env = {**os.environ, "DATABASE_URL": database_url}
    read = subprocess.run(
        [sys.executable, "-m", "db.try_block", "queries/holdings.sql", "holdings_for_account", "account_id=101"],
        cwd=SERVER_DIR, env=env, capture_output=True, text=True,
    )
    assert read.returncode == 0, read.stderr
    assert "SIM-WALLET-ALPHA" in read.stdout and "(1 row)" in read.stdout
    write = subprocess.run(
        [sys.executable, "-m", "db.try_block", "queries/transfer.sql", "reserve_idempotency_key,lock_positions",
         "actor_user_id=11", "source_account_id=101", "destination_account_id=102", "asset_id=1",
         "quantity=5", "idempotency_key=try-block-1", "request_hash=h"],
        cwd=SERVER_DIR, env=env, capture_output=True, text=True,
    )
    assert write.returncode == 0, write.stderr
    assert "-- lock_positions" in write.stdout and "Rolled back: nothing was saved." in write.stdout
    assert probe.rows_for_key(11, "try-block-1") == 0
    # A digits-only key is still TEXT (only *_id, *_cents and quantity become integers), so comparing
    # it with the TEXT column works instead of failing with "operator does not exist: text = smallint".
    digits = subprocess.run(
        [sys.executable, "-m", "db.try_block", "queries/transfer.sql", "find_existing_operation",
         "actor_user_id=11", "idempotency_key=2024"],
        cwd=SERVER_DIR, env=env, capture_output=True, text=True,
    )
    assert digits.returncode == 0, digits.stdout + digits.stderr
    assert "(0 rows)" in digits.stdout
    # *_cents is sent as an int, like the app sends it, so the receiver's -amount cash line works.
    before = probe.one("counts")
    cash = subprocess.run(
        [sys.executable, "-m", "db.try_block", "queries/settlement_engine.sql", "insert_dvp_cash_entries",
         f"settlement_operation_id={probe.value('any_operation_id')}", "business_date=2026-10-19",
         "deliverer_account_id=101", "receiver_account_id=102", "amount_cents=5"],
        cwd=SERVER_DIR, env=env, capture_output=True, text=True,
    )
    assert cash.returncode == 0, cash.stdout + cash.stderr
    assert "INSERT 0 2" in cash.stdout or "(2 rows)" in cash.stdout
    assert probe.one("counts") == before

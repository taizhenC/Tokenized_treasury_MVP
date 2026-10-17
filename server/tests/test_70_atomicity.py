"""Lessons 6-7: all or nothing. An error, or a killed connection, after the debit leaves no trace.

Also: the app recovers at once from a database restart, and a stuck row lock costs one request 5 s (lock_timeout), not the whole API.
"""

import time

import psycopg
import pytest

from app import queries
from app.domain import transfers
from app.domain.transactions import RowCountError
from tests.helpers import PROBES, Blocker, in_background, new_key, run, set_tokenized, wait_for_lock_waiters
from tests.spy import SpyConnection, SpyPool, spying

TRANSFER = queries.load("transfer", queries.TRANSFER_BLOCKS)
RECON = queries.load("reconciliation", queries.RECONCILIATION_BLOCKS)


def test_forced_error_after_the_debit_rolls_everything_back(users, probe):
    set_tokenized(users, probe, 101, 50)
    before = probe.snapshot()
    seen = {}

    def crash_after_debit(conn):
        seen["inside"] = run(conn, PROBES["tokenized_units"], {"account_id": 101}).fetchone()["tokenized_units"]
        seen["outside"] = probe.tokenized(101)  # another connection (READ COMMITTED)
        raise RuntimeError("simulated crash right after the debit")

    key = new_key("crash")
    with spying(after={"debit_source": crash_after_debit}):
        response = users["alpha"].transfer(102, 7, key)
    assert response.status_code == 500
    assert response.json() == {"error": "internal error"}
    assert seen == {"inside": 43, "outside": 50}  # debited inside the transaction only
    assert probe.snapshot() == before
    assert probe.rows_for_key(11, key) == 0

    retry = users["alpha"].transfer(102, 7, key)  # the key was never saved, so it works once
    assert retry.status_code == 201
    assert probe.rows_for_key(11, key) == 1


def test_an_unexpected_error_keeps_the_connection_usable(users, probe):
    """A 500 must not close the keep-alive connection behind the client's back. (Starlette's own
    catch-all re-raises after the 500 and uvicorn then closes the socket; on Linux the client's
    next read or request raced that close. app.errors.UnexpectedErrors answers without re-raising.)"""
    set_tokenized(users, probe, 101, 50)
    alpha = users["alpha"].fork()
    warm = alpha.get("/api/me")  # opens the connection

    def crash(conn):
        raise RuntimeError("simulated crash")

    with spying(after={"debit_source": crash}):
        crashed = alpha.transfer(102, 1, new_key("keepalive"))
    after = alpha.get("/api/me")
    assert crashed.status_code == 500 and crashed.json() == {"error": "internal error"}
    assert after.status_code == 200
    # One TCP connection served all three requests: the server never closed it.
    streams = {id(r.extensions["network_stream"]) for r in (warm, crashed, after)}
    assert len(streams) == 1
    alpha.close()


def test_connection_killed_after_the_debit_is_503_and_rolls_back(users, probe, database_url):
    set_tokenized(users, probe, 101, 50)
    before = probe.snapshot()
    key = new_key("kill")
    alpha = users["alpha"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_ledger_against_writes")  # the request will wait at insert_ledger_entries
        pending = in_background(lambda: alpha.transfer(102, 3, key))
        # By then it has reserved the key, locked both rows, debited and credited.
        waiter = wait_for_lock_waiters(probe, "INSERT INTO ledger_entries", 1)[0]
        assert probe.value("terminate_backend", pid=waiter["pid"]) is True
        response = pending.result(timeout=60)
    assert response.status_code == 503, response.text
    assert response.json()["error"] == "database unavailable, retry"
    assert probe.snapshot() == before
    assert probe.rows_for_key(11, key) == 0

    # The server process is fine: the lost connection surfaced as an exception, not a crash.
    assert users["alpha"].get("/api/health").json() == {"status": "ok", "database": "ok"}
    retry = users["alpha"].transfer(102, 3, key)
    assert retry.status_code == 201
    assert probe.rows_for_key(11, key) == 1


def test_rowcount_rule_turns_a_silent_no_op_debit_into_an_error(database_url, probe):
    """A slip that makes debit_source match no row (e.g. a wrong WHERE) is not an SQL error."""
    before = probe.snapshot()
    spy = SpyPool(None, rewrite_params={"debit_source": lambda params: {**params, "source_account_id": 999}})
    with psycopg.connect(database_url) as conn:
        with pytest.raises(RowCountError, match="debit_source: rowcount 0, expected 1"):
            transfers.transfer(SpyConnection(conn, spy), actor_user_id=11, source_account_id=101,
                               destination_account_id=102, asset_id=1, quantity=1,
                               idempotency_key=new_key("rowcount"))
    assert "credit_destination" not in spy.seen
    assert probe.snapshot() == before


def test_rowcount_rule_on_store_result_the_only_guard_there(users, probe):
    """store_result has no RETURNING, so nothing else would notice that it saved nothing."""
    set_tokenized(users, probe, 101, 50)
    before = probe.snapshot()
    key = new_key("rowcount-store")
    with spying(rewrite_params={"store_result": lambda params: {**params, "operation_id": 999999}}) as spy:
        response = users["alpha"].transfer(102, 5, key)
    assert response.status_code == 500
    assert spy.seen[-1] == "store_result"
    assert probe.snapshot() == before  # without the rule: committed with result NULL, replays would fail
    assert probe.rows_for_key(11, key) == 0


def test_without_the_rowcount_rule_units_appear_from_nothing(database_url, probe):
    """The contrast, run inside a transaction that is rolled back (no damage is kept)."""
    params = {"actor_user_id": 12, "source_account_id": 102, "destination_account_id": 103, "asset_id": 1,
              "quantity": 5, "idempotency_key": new_key("contrast"), "request_hash": "h"}
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            params["operation_id"] = run(conn, TRANSFER["reserve_idempotency_key"], params).fetchone()["id"]
            run(conn, TRANSFER["lock_positions"], params)
            missed = run(conn, TRANSFER["debit_source"], {**params, "source_account_id": 999})
            assert missed.rowcount == 0  # silently nothing
            run(conn, TRANSFER["credit_destination"], params)
            run(conn, TRANSFER["insert_ledger_entries"], params)
            conservation = run(conn, RECON["check_conservation"]).fetchall()
            buckets = run(conn, RECON["check_positions_vs_ledger"]).fetchall()
    assert conservation[0]["ok"] is False and conservation[0]["held_total"] == 3005
    assert [(b["account_id"], b["representation"]) for b in buckets if not b["ok"]] == [(102, "TOKENIZED")]
    assert probe.value("held_total") == 3000


def test_after_a_database_restart_the_next_request_is_served_at_once(users, probe):
    """A restart kills every pooled connection. psycopg_pool on its own meets the dead ones one at
    a time, sleeping 1, 2, 4, 8, 16 s in between (measured: 30 s, then 503). The check in
    app/db.py tests every idle connection as soon as one turns out to be dead."""
    from app.db import pool

    lent = [pool.getconn() for _ in range(8)]  # grow the pool: 8 idle connections afterwards
    for conn in lent:
        pool.putconn(conn)
    assert probe.value("terminate_other_connections") >= 8
    started = time.monotonic()
    response = users["alpha"].get("/api/me")
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    assert elapsed < 3, f"the first request after the restart took {elapsed:.1f} s"
    assert users["alpha"].get("/api/health").json() == {"status": "ok", "database": "ok"}


def test_a_stuck_row_lock_gives_503_after_the_lock_timeout_and_saves_nothing(users, probe, database_url):
    """Like a forgotten `try_block --hold` or psql BEGIN: the request gives up after lock_timeout
    (5 s, set on the pool's connections in app/db.py) instead of holding its connection forever."""
    set_tokenized(users, probe, 103, 10)
    before = probe.snapshot()
    key = new_key("stuck")
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=103)
        started = time.monotonic()
        response = users["gamma"].transfer(101, 1, key)
        waited = time.monotonic() - started
    assert response.status_code == 503, response.text
    assert response.json() == {"error": "database unavailable, retry", "pgCode": "55P03"}
    assert 4.5 < waited < 15
    assert probe.snapshot() == before
    assert probe.rows_for_key(13, key) == 0
    assert users["gamma"].transfer(101, 1, key).status_code == 201  # same key, once the lock is gone

"""Lessons 6-8 under real concurrency: many simultaneous HTTP requests against the live server,
each on its own thread and its own pooled connection.

To force an exact interleaving without sleeping, a test connection (Blocker) holds a row lock,
the requests queue up behind it (seen in pg_stat_activity), and then the lock is released.
"""

import threading

import psycopg
from psycopg import errors as pg

from tests.helpers import (
    PROBES,
    Blocker,
    in_background,
    new_key,
    run,
    run_together,
    set_tokenized,
    wait_for_lock_waiters,
)
from tests.spy import spying

INSUFFICIENT = {"error": "insufficient tokenized units"}


# ---------- A: two transfers of 80 from an account holding 100 ----------

def test_a_two_simultaneous_transfers_of_80_from_100_exactly_one_succeeds(users, probe, database_url):
    set_tokenized(users, probe, 103, 100)
    k1, k2 = new_key("A1"), new_key("A2")
    g1, g2 = users["gamma"].fork(), users["gamma"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=103)
        first = in_background(lambda: g1.transfer(101, 80, k1))
        second = in_background(lambda: g2.transfer(102, 80, k2))
        wait_for_lock_waiters(probe, "FOR UPDATE", 2)  # both are queued on gamma's row
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert sorted([r1.status_code, r2.status_code]) == [201, 422]
    loser = r1 if r1.status_code == 422 else r2
    assert loser.json() == INSUFFICIENT  # it read the balance AFTER the winner committed: 20
    assert probe.tokenized(103) == 20
    assert sorted([probe.rows_for_key(13, k1), probe.rows_for_key(13, k2)]) == [0, 1]


def test_a_stress_20_rounds_fired_together(users, probe):
    outcomes = []
    for _ in range(20):
        set_tokenized(users, probe, 103, 100)
        g1, g2 = users["gamma"].fork(), users["gamma"].fork()
        r1, r2 = run_together(
            lambda g1=g1: g1.transfer(101, 80, new_key("As")),
            lambda g2=g2: g2.transfer(102, 80, new_key("As")),
        )
        outcomes.append((sorted([r1.status_code, r2.status_code]), probe.tokenized(103)))
    assert outcomes == [([201, 422], 20)] * 20


def test_b_with_the_app_balance_check_fooled_the_check_constraint_stops_the_double_spend(users, probe, database_url):
    set_tokenized(users, probe, 103, 100)
    g1, g2 = users["gamma"].fork(), users["gamma"].fork()
    fool_the_app = {"lock_positions": lambda rows: [{**row, "tokenized_units": 10**6} for row in rows]}
    with spying(rewrite_rows=fool_the_app), Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=103)
        first = in_background(lambda: g1.transfer(101, 80, new_key("B")))
        second = in_background(lambda: g2.transfer(102, 80, new_key("B")))
        wait_for_lock_waiters(probe, "FOR UPDATE", 2)
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert sorted([r1.status_code, r2.status_code]) == [201, 422]
    loser = r1 if r1.status_code == 422 else r2
    backstop = {"error": "rule violated", "pgCode": "23514", "constraint": "positions_tokenized_units_check"}
    assert loser.json() == backstop
    assert probe.tokenized(103) == 20


def test_lesson6_two_simultaneous_mints_of_more_than_half(users, probe, database_url):
    quantity = probe.value("traditional_units", account_id=103) // 2 + 1
    g1, g2 = users["gamma"].fork(), users["gamma"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=103)
        first = in_background(lambda: g1.mint(quantity, new_key("M1")))
        second = in_background(lambda: g2.mint(quantity, new_key("M2")))
        wait_for_lock_waiters(probe, "FOR UPDATE", 2)
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert sorted([r1.status_code, r2.status_code]) == [201, 422]
    assert (r1 if r1.status_code == 422 else r2).json() == {"error": "insufficient traditional units"}


# ---------- D: the same request sent more than once at the same time ----------

def test_d_a_concurrent_duplicate_waits_on_the_unique_index_then_replays(users, probe, database_url):
    set_tokenized(users, probe, 101, 50)
    key = new_key("D")
    a1, a2 = users["alpha"].fork(), users["alpha"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=101)
        first = in_background(lambda: a1.transfer(102, 3, key))
        wait_for_lock_waiters(probe, "FOR UPDATE", 1)  # the first holds the key, waits for the row
        second = in_background(lambda: a2.transfer(102, 3, key))
        wait_for_lock_waiters(probe, "ON CONFLICT", 1)  # the duplicate waits on the unique index
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.json() == r1.json() and r2.headers["idempotency-replayed"] == "true"
    assert probe.rows_for_key(11, key) == 1
    assert len(probe.rows("ledger_lines_for_operation", operation_id=r1.json()["operationId"])) == 2
    assert probe.tokenized(101) == 47


def test_d2_if_the_first_holder_dies_the_waiting_duplicate_applies_it_once(users, probe, database_url):
    set_tokenized(users, probe, 101, 50)
    key = new_key("D2")
    a1, a2 = users["alpha"].fork(), users["alpha"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=101)
        first = in_background(lambda: a1.transfer(102, 2, key))
        holder = wait_for_lock_waiters(probe, "FOR UPDATE", 1)[0]
        second = in_background(lambda: a2.transfer(102, 2, key))
        wait_for_lock_waiters(probe, "ON CONFLICT", 1)
        probe.value("terminate_backend", pid=holder["pid"])  # the key holder's connection dies
        r1 = first.result(timeout=60)
        wait_for_lock_waiters(probe, "FOR UPDATE", 1)  # the waiter got the key, now waits for the row
        blocker.release()
        r2 = second.result(timeout=60)
    assert r1.status_code == 503 and r2.status_code == 201
    assert probe.rows_for_key(11, key) == 1
    assert probe.tokenized(101) == 48


def test_d3_same_key_different_body_at_the_same_time_is_201_and_409(users, probe, database_url):
    set_tokenized(users, probe, 101, 50)
    key = new_key("D3")
    a1, a2 = users["alpha"].fork(), users["alpha"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=101)
        first = in_background(lambda: a1.transfer(102, 2, key))
        wait_for_lock_waiters(probe, "FOR UPDATE", 1)
        second = in_background(lambda: a2.transfer(102, 4, key))
        wait_for_lock_waiters(probe, "ON CONFLICT", 1)
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert (r1.status_code, r2.status_code) == (201, 409)
    assert probe.rows_for_key(11, key) == 1
    assert probe.tokenized(101) == 48


def test_d4_six_identical_transfers_at_once_apply_once(users, probe):
    set_tokenized(users, probe, 101, 50)
    key = new_key("D4")
    clients = [users["alpha"].fork() for _ in range(6)]
    responses = run_together(*[lambda c=c: c.transfer(102, 1, key) for c in clients])
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 200, 201]
    assert len({str(sorted(r.json().items())) for r in responses}) == 1  # every replay = the original
    assert probe.tokenized(101) == 49
    assert probe.rows_for_key(11, key) == 1


def test_d4b_five_identical_mints_at_once_mint_once(users, probe):
    start = probe.tokenized(102)
    key = new_key("D4b")
    clients = [users["beta"].fork() for _ in range(5)]
    responses = run_together(*[lambda c=c: c.mint(4, key) for c in clients])
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert probe.tokenized(102) == start + 4


# ---------- E: opposite directions never deadlock ----------

def test_e_opposite_transfers_both_lock_the_lower_id_first(users, probe, database_url):
    set_tokenized(users, probe, 101, 50)
    set_tokenized(users, probe, 102, 50)
    beta, alpha = users["beta"].fork(), users["alpha"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=101)
        b_to_a = in_background(lambda: beta.transfer(101, 1, new_key("E")))
        a_to_b = in_background(lambda: alpha.transfer(102, 1, new_key("E")))
        wait_for_lock_waiters(probe, "FOR UPDATE", 2)
        # Both wait for 101 and NEITHER holds 102: with source-first locking, beta's request
        # would already hold 102 and this NOWAIT lock would fail with LockNotAvailable.
        with Blocker(database_url) as third:
            third.hold("lock_position_row_nowait", account_id=102)
        blocker.release()
        r1, r2 = b_to_a.result(timeout=60), a_to_b.result(timeout=60)
    assert (r1.status_code, r2.status_code) == (201, 201)


def test_e_30_rounds_of_4_transfers_in_all_directions_never_deadlock(users, probe):
    for account in (101, 102, 103):
        set_tokenized(users, probe, account, 60)
    statuses = []
    for _ in range(30):
        a1, a2, b, g = users["alpha"].fork(), users["alpha"].fork(), users["beta"].fork(), users["gamma"].fork()
        responses = run_together(
            lambda a1=a1: a1.transfer(102, 1, new_key("E")),
            lambda b=b: b.transfer(101, 1, new_key("E")),
            lambda g=g: g.transfer(101, 1, new_key("E")),
            lambda a2=a2: a2.transfer(103, 1, new_key("E")),
        )
        statuses += [r.status_code for r in responses]
    assert statuses == [201] * 120  # a deadlock (40P01) would have been a 503


def test_e_contrast_source_first_locking_in_two_statements_deadlocks(database_url):
    halfway = threading.Barrier(2)

    def lock_both(first: int, second: int) -> str:
        with psycopg.connect(database_url) as conn:
            try:
                with conn.transaction(force_rollback=True):
                    run(conn, PROBES["lock_position_row"], {"account_id": first})
                    halfway.wait()
                    run(conn, PROBES["lock_position_row"], {"account_id": second})
                return "ok"
            except pg.DeadlockDetected:
                return "40P01"

    results = run_together(lambda: lock_both(101, 102), lambda: lock_both(102, 101))
    assert sorted(results) == ["40P01", "ok"]


# ---------- F: ten transfers of 30 from 235 ----------

def test_f_ten_concurrent_transfers_of_30_from_235(users, probe):
    set_tokenized(users, probe, 103, 235)
    clients = [users["gamma"].fork() for _ in range(10)]
    responses = run_together(*[lambda c=c, i=i: c.transfer(101 if i % 2 else 102, 30, new_key("F"))
                               for i, c in enumerate(clients)])
    codes = [r.status_code for r in responses]
    assert (codes.count(201), codes.count(422)) == (7, 3)
    assert probe.tokenized(103) == 25


# ---------- H: operation ids follow RESERVATION order, not commit order ----------

def test_h_ids_are_unique_not_a_clock(users, probe, database_url):
    set_tokenized(users, probe, 101, 50)
    set_tokenized(users, probe, 103, 50)
    alpha = users["alpha"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=101)
        late = in_background(lambda: alpha.transfer(102, 7, new_key("H1")))  # reserves first, then waits
        wait_for_lock_waiters(probe, "FOR UPDATE", 1)
        early = users["gamma"].transfer(102, 5, new_key("H2"))  # reserves later, commits first
        blocker.release()
        late = late.result(timeout=60)
    assert early.status_code == 201 and late.status_code == 201
    first_id, second_id = late.json()["operationId"], early.json()["operationId"]
    assert first_id < second_id
    top_two = [r["id"] for r in users["beta"].get("/api/operations").json()[:2]]
    assert top_two == [second_id, first_id]  # newest id first, though it was applied FIRST
    applied = probe.rows("ledger_apply_order", account_id=102, operation_ids=[first_id, second_id])
    assert [r["operation_id"] for r in applied] == [second_id, first_id]  # ledger ids follow apply order

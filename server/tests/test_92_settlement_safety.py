"""Lessons 12-13 under stress: real concurrency, crashes in the middle of a settlement, the database
backstops, and the guards that stop one deliver order from settling twice.

Concurrency: every request that can settle or recycle a delivery locks the settlement_day row (the
SEQUENCER) first, then every position of the asset, then every settlement account - always in that
order. A mint or burn takes its own position, then its own settlement account (the same order); a
tokenized transfer takes two positions in account order. So requests queue, they never deadlock.
To force an exact interleaving without sleeping, a test connection (tests.helpers.Blocker) holds a
lock, the requests queue behind it (seen in pg_stat_activity), and then the lock is released.

Every test starts on an OPEN day with every net at 0 and nothing waiting, and leaves it so (the
fixture below closes the day when a test left money owed or an order waiting). Expected numbers are
relative to the state each test sets up (one unit = 9,500 cents of collateral).
"""

import psycopg
import pytest
from psycopg import errors as pg

from app import queries
from app.errors import classify_db_error
from tests.helpers import Blocker, in_background, new_key, run, run_together, set_tokenized, wait_for_lock_waiters
from tests.spy import spying
from tests.test_90_settlement import (
    CLOSED,
    MINT_BREACH,
    SETTLEMENT_PROBES,
    UNIT,
    WAITING,
    accounts,
    assert_books_balance,
    assert_flat,
    close_the_day,
    ensure_traditional,
    expect,
    instruction,
    set_traditional,
    snapshot,
    sql_one,
    sql_rows,
    start_flat,
)

ENGINE = queries.load("settlement_engine", queries.SETTLEMENT_ENGINE_BLOCKS)
INTERNAL_ERROR = {"error": "internal error"}
NDC_BACKSTOP = {"error": "net debit cap exceeded", "pgCode": "23514", "constraint": "settlement_within_net_debit_cap"}
CM_BACKSTOP = {"error": "collateral monitor would go negative", "pgCode": "23514",
               "constraint": "settlement_collateral_monitor_nonnegative"}


@pytest.fixture(autouse=True)
def flat_open_day(users, probe):
    start_flat(users, probe)
    yield
    start_flat(users, probe)


def hold_the_sequencer(blocker: Blocker) -> None:
    """Lock the settlement_day row on the blocker's connection until blocker.release()."""
    run(blocker.conn, SETTLEMENT_PROBES["lock_settlement_day"])


def crash(conn):
    raise RuntimeError("simulated crash in the middle of a settlement")


# ---------- one receiver, two deliveries: the cap holds ----------

def test_two_deliveries_into_one_receiver_each_fit_its_cap_alone_but_not_together(users, probe, database_url):
    ensure_traditional(users, probe, 102, 300)  # beta's CM can take a 1,000,000 debit
    for deliverer in (101, 103):
        ensure_traditional(users, probe, deliverer, 10)
    cap = accounts(probe)[102]["net_debit_cap_cents"]
    assert cap == 1_500_000  # each 1,000,000 fits alone; together 2,000,000 would not
    alpha, gamma = users["alpha"].fork(), users["gamma"].fork()
    with Blocker(database_url) as blocker:
        hold_the_sequencer(blocker)
        first = in_background(lambda: alpha.deliver(102, 1, 1_000_000, new_key("cap-a")))
        second = in_background(lambda: gamma.deliver(102, 1, 1_000_000, new_key("cap-g")))
        wait_for_lock_waiters(probe, "settlement_day", 2)  # both reserved their key, both queue
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert sorted([r1.status_code, r2.status_code]) == [201, 202]
    recycled = (r1 if r1.status_code == 202 else r2).json()
    assert recycled["recycleReason"] == WAITING  # the receiver's cap: masked for the deliverer
    assert instruction(probe, recycled["instructionId"])["recycle_reason"] == "RECEIVER_NET_DEBIT_CAP"
    assert accounts(probe)[102]["net_cents"] == -1_000_000
    # beta pays 500,000: -500,000 - 1,000,000 = -1,500,000, exactly the cap - which is allowed
    paid = expect(users["beta"].pay(500_000, new_key("cap-pay")), 201)
    assert paid["releasedInstructionIds"] == [recycled["instructionId"]]
    assert accounts(probe)[102]["net_cents"] == -cap
    assert_books_balance(users)


def test_two_deliveries_into_one_receiver_fired_together_10_rounds(users, probe):
    ensure_traditional(users, probe, 102, 300)
    for deliverer in (101, 103):
        ensure_traditional(users, probe, deliverer, 20)
    outcomes = []
    for _ in range(10):
        start_flat(users, probe)
        alpha, gamma = users["alpha"].fork(), users["gamma"].fork()
        responses = run_together(
            lambda alpha=alpha: alpha.deliver(102, 1, 1_000_000, new_key("cap-r")),
            lambda gamma=gamma: gamma.deliver(102, 1, 1_000_000, new_key("cap-r")),
        )
        outcomes.append((sorted(r.status_code for r in responses), accounts(probe)[102]["net_cents"]))
        close_the_day(users)  # drops the recycled order, every net back to 0
    assert outcomes == [([201, 202], -1_000_000)] * 10


# ---------- the lock order ----------

def test_the_engine_locks_the_sequencer_then_every_position_then_the_settlement_accounts(users, probe, database_url):
    for account_id in (101, 102):
        ensure_traditional(users, probe, account_id, 10)
    alpha, beta = users["alpha"].fork(), users["beta"].fork()
    with Blocker(database_url) as blocker:
        blocker.hold("lock_position_row", account_id=103)  # gamma's row: part of NEITHER delivery
        first = in_background(lambda: alpha.deliver(102, 1, UNIT, new_key("order-a")))
        # It took the sequencer, locked positions 101 and 102 (account order) and waits for 103:
        # the engine locks every position up front, because a retry may settle anybody's order.
        wait_for_lock_waiters(probe, "FROM positions", 1)
        second = in_background(lambda: beta.deliver(101, 1, UNIT, new_key("order-b")))
        wait_for_lock_waiters(probe, "settlement_day", 1)  # the next engine request queues on the sequencer
        with Blocker(database_url) as third:
            with pytest.raises(pg.LockNotAvailable):
                third.hold("lock_position_row_nowait", account_id=102)  # already held by the first request
        with Blocker(database_url) as fourth:
            # Free: settlement accounts come AFTER all positions, and the first request is not there yet.
            run(fourth.conn, SETTLEMENT_PROBES["lock_settlement_account_nowait"], {"account_id": 101})
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert (r1.status_code, r2.status_code) == (201, 201)
    assert_books_balance(users)


def test_deliveries_racing_transfers_mints_burns_and_payments_never_deadlock(users, probe):
    for account_id in (101, 102, 103):
        ensure_traditional(users, probe, account_id, 60)
    for account_id in (101, 103):
        set_tokenized(users, probe, account_id, 40)
    for account_id in (101, 102, 103):
        ensure_traditional(users, probe, account_id, 30)
    rounds = 15
    statuses = []
    for _ in range(rounds):
        a1, a2, b1, b2, b3, g1, g2, g3 = (users[name].fork() for name in (
            "alpha", "alpha", "beta", "beta", "beta", "gamma", "gamma", "gamma"))
        # Deliveries at exactly one unit's collateral value (9,500) leave every CM unchanged, so
        # every request below must succeed - whatever order the database runs them in.
        responses = run_together(
            lambda a1=a1: a1.deliver(102, 1, UNIT, new_key("race")),  # engine
            lambda b1=b1: b1.deliver(103, 1, UNIT, new_key("race")),  # engine
            lambda g1=g1: g1.deliver(101, 1, UNIT, new_key("race")),  # engine
            lambda b3=b3: b3.pay(1, new_key("race")),  # engine
            lambda a2=a2: a2.transfer(103, 1, new_key("race")),  # two positions
            lambda g2=g2: g2.transfer(102, 1, new_key("race")),  # two positions
            lambda b2=b2: b2.mint(1, new_key("race")),  # own position, then own settlement account
            lambda g3=g3: g3.burn(1, new_key("race")),  # own position, then own settlement account
        )
        statuses += [r.status_code for r in responses]
    assert statuses == [201] * (8 * rounds)  # a deadlock (40P01) or a lock timeout would be a 503
    result = assert_books_balance(users)
    assert result["conservation"][0]["held_total"] == 3000
    assert all(r["net"] == 0 for r in sql_rows(probe, "cash_net_per_operation"))


def test_deliveries_racing_the_valued_cutoff_either_beat_it_or_are_refused(users, probe):
    for account_id in (101, 102, 103):
        ensure_traditional(users, probe, account_id, 20)
    for _ in range(5):
        start_flat(users, probe)
        a1, a2, b, g, op = (users[name].fork() for name in ("alpha", "alpha", "beta", "gamma", "operator"))
        *deliveries, cutoff = run_together(
            lambda a1=a1: a1.deliver(102, 1, UNIT, new_key("vs-cutoff")),
            lambda a2=a2: a2.deliver(104, 1, 100, new_key("vs-cutoff")),  # Delta's $0 cap: recycles if in time
            lambda b=b: b.deliver(103, 1, UNIT, new_key("vs-cutoff")),
            lambda g=g: g.deliver(101, 1, UNIT, new_key("vs-cutoff")),
            lambda op=op: op.valued_cutoff(new_key("vs-cutoff")),
        )
        dropped = expect(cutoff, 201)["droppedInstructionIds"]
        recycled = []
        for response in deliveries:
            body = response.json()
            if response.status_code == 422:  # queued behind the cutoff on the sequencer
                assert body == {"error": CLOSED}
                continue
            assert (response.status_code, body["status"]) in {(201, "SETTLED"), (202, "RECYCLING")}
            if response.status_code == 202:
                recycled.append(body["instructionId"])
        assert dropped == sorted(recycled)  # every order that got in before the cutoff and waited is dropped
        assert sql_one(probe, "day_status")["recycling"] == 0
        close_the_day(users)
        assert_books_balance(users)


def test_a_delivery_queued_behind_the_cutoff_on_the_sequencer_is_refused(users, probe, database_url):
    """The race above, forced into one order: the cutoff queues on the sequencer first, a delivery
    second. Postgres hands a row lock to its waiters in arrival order, so the cutoff runs first and
    closes valued processing; the delivery then reads the day only once it holds the lock, sees the
    new phase and is refused - with nothing saved."""
    ensure_traditional(users, probe, 101, 1)
    alpha, operator = users["alpha"].fork(), users["operator"].fork()
    key = new_key("behind-cutoff")
    with Blocker(database_url) as blocker:
        hold_the_sequencer(blocker)
        cutoff = in_background(lambda: operator.valued_cutoff(new_key("cutoff-first")))
        wait_for_lock_waiters(probe, "settlement_day", 1)
        late = in_background(lambda: alpha.deliver(104, 1, 100, key))  # Delta's $0 cap: it would recycle
        wait_for_lock_waiters(probe, "settlement_day", 2)
        blocker.release()
        first, second = cutoff.result(timeout=60), late.result(timeout=60)
    assert expect(first, 201)["droppedInstructionIds"] == []  # the delivery never got in before it
    assert (second.status_code, second.json()) == (422, {"error": CLOSED})
    assert probe.rows_for_key(11, key) == 0  # its reserved key was rolled back with it
    assert sql_one(probe, "day_status")["recycling"] == 0


@pytest.mark.parametrize("direction", ["mint", "burn"])
def test_a_conversion_locks_its_position_before_its_settlement_account(users, probe, database_url, direction):
    """Mint and burn take their own position, then their own settlement account: a subset of the
    global order. With beta's settlement account held, a conversion queues on that row - while it
    already holds beta's position, which nobody else can now lock."""
    ensure_traditional(users, probe, 102, 10)
    set_tokenized(users, probe, 102, 5)
    beta = users["beta"].fork()
    convert = beta.mint if direction == "mint" else beta.burn
    with Blocker(database_url) as blocker:
        run(blocker.conn, SETTLEMENT_PROBES["lock_settlement_account_nowait"], {"account_id": 102})
        response = in_background(lambda: convert(1, new_key(f"{direction}-lock-order")))
        wait_for_lock_waiters(probe, "FROM settlement_accounts", 1)
        with Blocker(database_url) as third:
            with pytest.raises(pg.LockNotAvailable):
                third.hold("lock_position_row_nowait", account_id=102)  # the conversion holds it already
        blocker.release()
        assert response.result(timeout=60).status_code == 201
    assert_books_balance(users)


# ---------- crashes: all or nothing ----------

def test_a_crash_after_receive_units_leaves_no_trace(users, probe):
    ensure_traditional(users, probe, 101, 10)
    before = snapshot(probe)
    key = new_key("crash-units")
    with spying(after={"receive_units": crash}) as spy:
        response = users["alpha"].deliver(102, 3, 3 * UNIT, key)
    assert (response.status_code, response.json()) == (500, INTERNAL_ERROR)
    assert spy.seen[-3:] == ["insert_dvp_settlement_operation", "deliver_units", "receive_units"]
    assert snapshot(probe) == before  # no units moved, no settlement event, no instruction
    assert probe.rows_for_key(11, key) == 0
    retry = expect(users["alpha"].deliver(102, 3, 3 * UNIT, key), 201)  # the key was never saved
    assert retry["status"] == "SETTLED"


def test_a_crash_after_the_receivers_debit_rolls_back_both_legs(users, probe):
    ensure_traditional(users, probe, 101, 10)
    before = snapshot(probe)
    net_before = accounts(probe)[102]["net_cents"]
    seen = {}

    def crash_after_debit(conn):  # runs inside the request's transaction, right after the debit
        seen["inside"] = run(conn, SETTLEMENT_PROBES["net_cents"], {"account_id": 102}).fetchone()["net_cents"]
        seen["outside"] = sql_one(probe, "net_cents", account_id=102)["net_cents"]  # another connection
        raise RuntimeError("simulated crash right after the receiver's debit")

    key = new_key("crash-money")
    with spying(after={"debit_receiver_settlement": crash_after_debit}):
        response = users["alpha"].deliver(102, 2, 2 * UNIT, key)
    assert (response.status_code, response.json()) == (500, INTERNAL_ERROR)
    assert seen == {"inside": net_before - 2 * UNIT, "outside": net_before}  # debited inside the transaction only
    assert snapshot(probe) == before  # the units, the deliverer's credit and the debit: all gone
    assert probe.rows_for_key(11, key) == 0
    expect(users["alpha"].deliver(102, 2, 2 * UNIT, key), 201)
    assert accounts(probe)[102]["net_cents"] == net_before - 2 * UNIT


def test_a_crash_inside_the_retry_rolls_back_the_payment_that_triggered_it(users, probe):
    ensure_traditional(users, probe, 102, 300)
    ensure_traditional(users, probe, 101, 10)
    # beta's net would be -1,600,000 < -1,500,000: the order waits for a credit to beta
    waiting = expect(users["alpha"].deliver(102, 1, 1_600_000, new_key("waits")), 202)
    before = snapshot(probe)
    key = new_key("pay-crash")
    with spying(after={"receive_units": crash}) as spy:  # the retry settles the order: crash there
        response = users["beta"].pay(200_000, key)
    assert (response.status_code, response.json()) == (500, INTERNAL_ERROR)
    assert "credit_settlement_payment" in spy.seen and spy.seen[-1] == "receive_units"
    assert snapshot(probe) == before  # no credit, no cash lines, the order still RECYCLING with its reason
    assert probe.rows_for_key(12, key) == 0
    paid = expect(users["beta"].pay(200_000, key), 201)
    assert paid["releasedInstructionIds"] == [waiting["instructionId"]]


# ---------- the database backstops: the app's check fooled, the CHECK still holds ----------

def test_backstop_a_faked_net_debit_cap_is_stopped_by_the_check_constraint(users, probe):
    ensure_traditional(users, probe, 102, 300)  # beta's CM is not the problem: 300 x 9,500 > 1,600,000
    ensure_traditional(users, probe, 101, 10)
    before = snapshot(probe)
    key = new_key("fake-cap")
    huge_caps = {"lock_settlement_accounts": lambda rows: [{**row, "net_debit_cap_cents": 10**15} for row in rows]}
    with spying(rewrite_rows=huge_caps) as spy:
        response = users["alpha"].deliver(102, 1, 1_600_000, key)
    assert (response.status_code, response.json()) == (422, NDC_BACKSTOP)
    # The app believed the cap and settled: the deliverer's leg ran, the receiver's debit was refused.
    assert spy.seen[-1] == "credit_deliverer_settlement"
    assert snapshot(probe) == before
    assert probe.rows_for_key(11, key) == 0
    honest = expect(users["alpha"].deliver(102, 1, 1_600_000, key), 202)  # without the spy: it recycles
    assert instruction(probe, honest["instructionId"])["recycle_reason"] == "RECEIVER_NET_DEBIT_CAP"


def test_backstop_a_faked_collateral_value_is_stopped_by_the_check_constraint(users, probe):
    ensure_traditional(users, probe, 102, 10)
    set_traditional(users, probe, 103, 10)  # gamma: collateral 95,000, cap 2,500,000
    before = snapshot(probe)
    key = new_key("fake-collateral")
    rich_gamma = {"lock_settlement_accounts": lambda rows: [
        {**row, "collateral_value_cents": 10**12} if row["account_id"] == 103 else row for row in rows]}
    with spying(rewrite_rows=rich_gamma) as spy:
        response = users["beta"].deliver(103, 1, 200_000, key)  # really: 95,000 + 9,500 - 200,000 = -95,500
    assert (response.status_code, response.json()) == (422, CM_BACKSTOP)
    assert spy.seen[-1] == "credit_deliverer_settlement"
    assert snapshot(probe) == before
    honest = expect(users["beta"].deliver(103, 1, 200_000, key), 202)
    assert instruction(probe, honest["instructionId"])["recycle_reason"] == "RECEIVER_COLLATERAL_MONITOR"


def test_backstop_a_mint_with_a_faked_collateral_value_is_stopped_by_the_check_constraint(users, probe):
    ensure_traditional(users, probe, 102, 10)
    set_traditional(users, probe, 103, 10)
    # gamma takes 1 unit for 104,500: CM (10 + 1) x 9,500 - 104,500 = exactly 0
    expect(users["beta"].deliver(103, 1, 11 * UNIT, new_key("to-cm-zero")), 201)
    assert accounts(probe)[103]["collateral_monitor_cents"] == 0
    before = snapshot(probe)
    honest = users["gamma"].mint(1, new_key("mint-honest"))
    assert (honest.status_code, honest.json()) == (422, {"error": MINT_BREACH})  # the app's own check
    rich = {"lock_settlement_account": lambda rows: [{**row, "collateral_value_cents": 10**12} for row in rows]}
    with spying(rewrite_rows=rich) as spy:
        fooled = users["gamma"].mint(1, new_key("mint-fooled"))
    assert (fooled.status_code, fooled.json()) == (422, CM_BACKSTOP)
    assert spy.seen[-1] == "apply_conversion"  # update_collateral was the statement refused
    assert snapshot(probe) == before


# ---------- one order settles once ----------

def test_a_second_settlement_event_for_the_same_order_is_refused_by_the_system_key(users, probe, database_url):
    ensure_traditional(users, probe, 101, 10)
    order = expect(users["alpha"].deliver(102, 1, UNIT, new_key("once")), 201)
    params = {"submitted_operation_id": order["operationId"], "deliverer_account_id": 101,
              "receiver_account_id": 102, "asset_id": 1, "quantity": 1, "amount_cents": UNIT}
    before = snapshot(probe)
    with psycopg.connect(database_url) as conn:
        with pytest.raises(pg.UniqueViolation) as caught:
            with conn.transaction():
                run(conn, ENGINE["insert_dvp_settlement_operation"], params)
        # NULLS NOT DISTINCT: two rows with actor NULL and the key 'dvp-<order>' count as duplicates.
        assert caught.value.diag.constraint_name == "operations_actor_key_unique"
        assert classify_db_error(caught.value)[0] == 409
        # And the order itself can be marked settled only while it is RECYCLING (rowcount 0 here;
        # the app's rowcount rule turns that into an error).
        with conn.transaction(force_rollback=True):
            marked = run(conn, ENGINE["mark_instruction_settled"], {
                "instruction_id": order["instructionId"], "settlement_operation_id": order["settlementOperationId"]})
            assert marked.rowcount == 0
    assert sql_one(probe, "settlements_for_order", submitted_operation_id=order["operationId"])["n"] == 1
    assert snapshot(probe) == before


def test_two_payments_racing_to_release_one_order_settle_it_once(users, probe, database_url):
    ensure_traditional(users, probe, 102, 300)
    ensure_traditional(users, probe, 101, 10)
    net_before = accounts(probe)[102]["net_cents"]
    waiting = expect(users["alpha"].deliver(102, 1, 1_600_000, new_key("race-wait")), 202)
    b1, b2 = users["beta"].fork(), users["beta"].fork()
    with Blocker(database_url) as blocker:
        hold_the_sequencer(blocker)
        first = in_background(lambda: b1.pay(200_000, new_key("race-pay")))
        second = in_background(lambda: b2.pay(200_000, new_key("race-pay")))
        wait_for_lock_waiters(probe, "settlement_day", 2)
        blocker.release()
        r1, r2 = first.result(timeout=60), second.result(timeout=60)
    assert (r1.status_code, r2.status_code) == (201, 201)
    # Each payment alone frees the order (-1,600,000 + 200,000 >= -1,500,000). The second one reads
    # the waiting list only after it holds the sequencer, so it finds the order already settled.
    released = sorted([r1.json()["releasedInstructionIds"], r2.json()["releasedInstructionIds"]])
    assert released == [[], [waiting["instructionId"]]]
    assert sql_one(probe, "settlements_for_order", submitted_operation_id=waiting["operationId"])["n"] == 1
    assert accounts(probe)[102]["net_cents"] == net_before - 1_600_000 + 2 * 200_000
    assert_books_balance(users)


def test_the_module_leaves_the_day_open_flat_and_the_books_balanced(users, probe):
    close_the_day(users)
    assert_flat(probe)
    assert_books_balance(users)

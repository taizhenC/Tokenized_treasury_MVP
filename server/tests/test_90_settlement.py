"""Lessons 12-14: DVP settlement through the real HTTP API - every flow, every message, the numbers.

Day 1 is the demo scenario (README, settlement section) with its EXACT numbers. Earlier modules
moved balances around, so the module first puts every participant back to the seed's positions
(1,000 traditional, 0 tokenized) through the API, on a flat day (every net 0, nothing waiting).
Day 2 covers the rest: each of the four recycle reasons, the exact collateral-monitor boundary for a
delivery and for a mint, a release caused by a third party (hidden from it), and the valued cutoff
dropping what can never settle. Day 3 pins the retry itself: a waiting order's reason kept current,
the retry order (largest amount first, then oldest), a release that needs a second pass, and the
largest amount the API accepts. Days 2 and 3 follow from the state their own steps set up, with the
risk.py arithmetic (one unit = 9,500 cents of collateral):
    deliverer CM after = collateral - q x 9,500 + net + amount          must stay >= 0
    receiver  CM after = collateral + q x 9,500 + net - amount          must stay >= 0
    receiver net after = net - amount                                   must stay >= -net debit cap
The steps share state and run in file order, like test_50. The module leaves the day OPEN and flat.

The helpers below (plain functions: no fixtures, no test_ names) are also used by test_85, test_92
and test_99.
"""

from datetime import date, timedelta
from typing import Any

import httpx
import psycopg
import pytest

from app import queries
from app.sql import load_sql_blocks
from tests.helpers import PARTICIPANT_OF, SEEDED_POSITIONS, TESTS_DIR, Probe, new_key, run, set_tokenized
from tests.spy import spying

SETTLEMENT_PROBES = load_sql_blocks(TESTS_DIR / "sql" / "settlement_probes.sql")
FINAL_SETTLEMENT = queries.load("final_settlement", queries.FINAL_SETTLEMENT_BLOCKS)

UNIT = 9_500  # collateral value of ONE traditional unit, in cents: $100.00 prior close less the 5% haircut
WAITING = "WAITING_ON_COUNTERPARTY"  # what a party sees for the OTHER side's recycle reason
CLOSED = "valued processing is closed: the valued cutoff has passed"
MINT_BREACH = "collateral monitor would go negative: tokenized units have no collateral value"
REUSED = {"error": "idempotency key reused for a different request"}
SETTLEMENT_CHECKS = (
    "cashLinesNetToZero", "settlementVsCashLedger", "collateralVsPositions",
    "settledInstructionsVsLedgers", "riskControls", "closedDays",
)
VIEWERS = ("alpha", "beta", "gamma", "auditor", "operator")


# ---------- helpers (shared with test_85, test_92, test_99) ----------

def sql_rows(probe: Probe, block: str, /, **params: Any) -> list[dict[str, Any]]:
    """Run a block of tests/sql/settlement_probes.sql on the probe connection."""
    return run(probe.conn, SETTLEMENT_PROBES[block], params).fetchall()


def sql_one(probe: Probe, block: str, /, **params: Any) -> dict[str, Any]:
    return sql_rows(probe, block, **params)[0]


def expect(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return response.json()


def accounts(probe: Probe) -> dict[int, dict[str, Any]]:
    """Every account's units, net, collateral, CM and cap, by account id."""
    return {row["account_id"]: row for row in sql_rows(probe, "settlement_state")}


def nets(probe: Probe) -> dict[int, int]:
    return {account_id: row["net_cents"] for account_id, row in accounts(probe).items()}


def figures_of(probe: Probe, account_id: int) -> dict[str, int]:
    """The `account` object of a deliver-order or payment answer, as the database has it now."""
    row = accounts(probe)[account_id]
    return {
        "netCents": row["net_cents"],
        "collateralValueCents": row["collateral_value_cents"],
        "collateralMonitorCents": row["collateral_monitor_cents"],
        "netDebitCapCents": row["net_debit_cap_cents"],
        "traditionalUnits": row["traditional_units"],
    }


def instruction(probe: Probe, instruction_id: int) -> dict[str, Any]:
    """One deliver order with its EXACT recycle reason."""
    return sql_one(probe, "instruction", instruction_id=instruction_id)


def snapshot(probe: Probe) -> dict[str, Any]:
    """Everything a refused, replayed or crashed settlement request must leave unchanged."""
    return {
        **probe.snapshot(),
        **sql_one(probe, "settlement_counts"),
        "settlement": sql_rows(probe, "settlement_state"),
        "day": sql_one(probe, "day_status"),
        "instructions": sql_rows(probe, "instruction_statuses"),
    }


def report(users: dict) -> dict[str, Any]:
    return expect(users["auditor"].get("/api/reconciliation"), 200)


def failing_checks(result: dict[str, Any]) -> set[str]:
    """The report keys with at least one row that is not ok."""
    return {key for key, rows in result.items()
            if isinstance(rows, list) and any(row["ok"] is not True for row in rows)}


def assert_books_balance(users: dict) -> dict[str, Any]:
    result = report(users)
    assert failing_checks(result) == set() and result["passed"] is True, result
    assert all(key in result for key in SETTLEMENT_CHECKS)
    return result


def close_the_day(users: dict) -> dict[str, Any]:
    """The operator's two end-of-day steps: the valued cutoff (unless it already ran today), then
    final settlement. Every net goes back to 0 and the next business day - the next weekday - opens."""
    operator = users["operator"]
    if expect(operator.get("/api/settlement/day"), 200)["phase"] == "OPEN":
        expect(operator.valued_cutoff(new_key("cutoff")), 201)
    final = expect(operator.final_settlement(new_key("final")), 201)
    assert final["nextBusinessDate"] == next_weekday(date.fromisoformat(final["businessDate"])).isoformat()
    return final


def assert_flat(probe: Probe) -> dict[str, Any]:
    """The day is OPEN, every net is 0 and no order is waiting."""
    status = sql_one(probe, "day_status")
    assert (status["phase"], status["accounts_not_flat"], status["recycling"]) == ("OPEN", 0, 0), status
    return status


def start_flat(users: dict, probe: Probe) -> dict[str, Any]:
    """A flat OPEN day to start from: close the current day first if it is not flat."""
    status = sql_one(probe, "day_status")
    if (status["phase"], status["accounts_not_flat"], status["recycling"]) != ("OPEN", 0, 0):
        close_the_day(users)
    return assert_flat(probe)


def set_traditional(users: dict, probe: Probe, account_id: int, target: int) -> None:
    """Bring an account's TRADITIONAL units to `target` inside the account: mint the excess, or burn
    tokenized units back (receiving tokenized units from another participant first if it has too few).
    A mint lowers the collateral, so call this when the account can take it (e.g. its net is 0)."""
    api = users[PARTICIPANT_OF[account_id]]
    current = probe.value("traditional_units", account_id=account_id)
    if current > target:
        expect(api.mint(current - target, new_key("trim")), 201)
    elif current < target:
        need = target - current
        if probe.tokenized(account_id) < need:
            set_tokenized(users, probe, account_id, need)
        expect(api.burn(need, new_key("top-up")), 201)
    assert probe.value("traditional_units", account_id=account_id) == target


def ensure_traditional(users: dict, probe: Probe, account_id: int, minimum: int) -> None:
    if probe.value("traditional_units", account_id=account_id) < minimum:
        set_traditional(users, probe, account_id, minimum)


def reset_positions(users: dict, probe: Probe) -> None:
    """Through the API only: every participant back to 1,000 traditional / 0 tokenized (the seed).
    Tokenized transfers even out the totals, burns turn every tokenized unit back. Needs a flat day:
    with every net at 0 no mint can push a collateral monitor below zero."""
    rows = {row["account_id"]: row for row in probe.rows("positions")}
    assert (rows[104]["traditional_units"], rows[104]["tokenized_units"]) == (0, 0)
    surplus = {a: rows[a]["traditional_units"] + rows[a]["tokenized_units"] - 1000 for a in PARTICIPANT_OF}
    for giver in PARTICIPANT_OF:
        for taker in PARTICIPANT_OF:
            move = min(surplus[giver], -surplus[taker])
            if giver == taker or move <= 0:
                continue
            api = users[PARTICIPANT_OF[giver]]
            short = move - probe.tokenized(giver)
            if short > 0:
                expect(api.mint(short, new_key("reset-mint")), 201)
            expect(api.transfer(taker, move, new_key("reset-move")), 201)
            surplus[giver] -= move
            surplus[taker] += move
    for account_id, name in PARTICIPANT_OF.items():
        tokenized = probe.tokenized(account_id)
        if tokenized:
            expect(users[name].burn(tokenized, new_key("reset-burn")), 201)
    assert probe.positions() == SEEDED_POSITIONS


def next_weekday(day: date) -> date:
    """The next business day of this lab: the next Monday-to-Friday date (no holiday calendar)."""
    day += timedelta(days=1)
    while day.weekday() >= 5:  # Saturday 5, Sunday 6
        day += timedelta(days=1)
    return day


def seen_by(users: dict, instruction_id: int) -> dict[str, tuple[str, str | None] | None]:
    """What each user's GET /api/delivery-orders shows for one order: (status, recycle_reason),
    or None when the order is not in that user's list at all."""
    seen: dict[str, tuple[str, str | None] | None] = {}
    for name in VIEWERS:
        rows = expect(users[name].get("/api/delivery-orders"), 200)
        row = next((r for r in rows if r["id"] == instruction_id), None)
        seen[name] = None if row is None else (row["status"], row["recycle_reason"])
    return seen


NOT_LISTED = False  # for everyone_sees: not a party to the order, so it is not in that participant's list


def everyone_sees(status: str, *, alpha: Any, beta: Any, gamma: Any, exact: str | None) -> dict[str, Any]:
    """seen_by's expected value. Each participant gets the reason it should see (None once settled)
    or NOT_LISTED; the auditor and the operator always see the exact reason."""
    def view(reason: Any) -> Any:
        return None if reason is NOT_LISTED else (status, reason)
    return {"alpha": view(alpha), "beta": view(beta), "gamma": view(gamma),
            "auditor": (status, exact), "operator": (status, exact)}


STEP: dict[str, Any] = {}  # answers and keys of the steps, by name


@pytest.fixture(scope="module", autouse=True)
def the_seed_positions_on_a_flat_day(users, probe):
    start_flat(users, probe)
    reset_positions(users, probe)
    yield
    start_flat(users, probe)  # leave the day OPEN and flat for the modules after this one


# ---------- Day 1: the demo scenario, exact numbers ----------

def test_d1_the_day_opens_flat_at_the_seed_positions(users, probe):
    status = assert_flat(probe)
    STEP["d1"] = status["business_date"].isoformat()
    assert status["business_date"].weekday() < 5  # a business day is a weekday (Monday 0 ... Friday 4)
    assert expect(users["alpha"].get("/api/settlement/day"), 200) == {
        "business_date": STEP["d1"], "phase": "OPEN", "max_net_debit_cap_cents": 3_844_000,
        "prior_close_cents": 10_000, "haircut_bps": 500, "collateral_cents_per_unit": UNIT,
    }
    assert probe.positions() == SEEDED_POSITIONS
    # collateral = traditional units x 9,500 (tokenized count 0); caps from the seed's formula
    assert [(a, r["net_cents"], r["collateral_value_cents"], r["net_debit_cap_cents"])
            for a, r in accounts(probe).items()] == [
        (101, 0, 9_500_000, 3_844_000), (102, 0, 9_500_000, 1_500_000),
        (103, 0, 9_500_000, 2_500_000), (104, 0, 0, 0),
    ]
    assert_books_balance(users)


def test_d1_step1_alpha_delivers_200_to_beta_and_it_recycles_on_betas_cap(users, probe):
    before = snapshot(probe)
    key = new_key("d1-order-1")
    body = expect(users["alpha"].deliver(102, 200, 2_000_000, key), 202)
    assert body == {
        "operationId": body["operationId"], "type": "DELIVER_ORDER", "instructionId": body["instructionId"],
        "businessDate": STEP["d1"], "fromAccountId": 101, "toAccountId": 102, "quantity": 200,
        "amountCents": 2_000_000, "status": "RECYCLING",
        # beta's net would be -2,000,000 < -1,500,000: a RECEIVER reason, so alpha only learns that it waits
        "recycleReason": WAITING, "settlementOperationId": None, "releasedInstructionIds": [],
        "account": {"netCents": 0, "collateralValueCents": 9_500_000, "collateralMonitorCents": 9_500_000,
                    "netDebitCapCents": 3_844_000, "traditionalUnits": 1000},
    }
    after = snapshot(probe)
    # Recorded (the DELIVER_ORDER request and its instruction), but nothing moved: no units, no money.
    for unchanged in ("positions", "settlement", "ledger_entries", "cash_ledger_entries"):
        assert after[unchanged] == before[unchanged], unchanged
    assert (after["operations"], after["delivery_instructions"]) == (
        before["operations"] + 1, before["delivery_instructions"] + 1)
    row = instruction(probe, body["instructionId"])
    assert (row["status"], row["recycle_reason"], row["settlement_operation_id"], row["submitted_operation_id"],
            row["business_date"].isoformat()) == (
        "RECYCLING", "RECEIVER_NET_DEBIT_CAP", None, body["operationId"], STEP["d1"])
    op = sql_one(probe, "operation", operation_id=body["operationId"])
    assert (op["operation_type"], op["actor_user_id"], op["source_account_id"], op["destination_account_id"],
            op["asset_id"], op["quantity"], op["amount_cents"], op["idempotency_key"]) == (
        "DELIVER_ORDER", 11, 101, 102, 1, 200, 2_000_000, key)
    STEP["order1"] = {"key": key, "body": body}
    assert_books_balance(users)


def test_d1_each_side_sees_only_its_own_reason_and_a_third_party_sees_nothing(users):
    i1 = STEP["order1"]["body"]["instructionId"]
    assert seen_by(users, i1) == everyone_sees(
        "RECYCLING", alpha=WAITING, beta="RECEIVER_NET_DEBIT_CAP", gamma=NOT_LISTED, exact="RECEIVER_NET_DEBIT_CAP")
    row = next(r for r in expect(users["beta"].get("/api/delivery-orders"), 200) if r["id"] == i1)
    assert {k: row[k] for k in ("business_date", "deliverer_account_id", "receiver_account_id", "quantity",
                                "amount_cents", "submitted_operation_id", "settlement_operation_id",
                                "submitted_by", "settled_at")} == {
        "business_date": STEP["d1"], "deliverer_account_id": 101, "receiver_account_id": 102, "quantity": 200,
        "amount_cents": 2_000_000, "submitted_operation_id": STEP["order1"]["body"]["operationId"],
        "settlement_operation_id": None, "submitted_by": "alpha", "settled_at": None,
    }
    assert row["submitted_at"] is not None


def test_d1_step2_beta_delivers_100_to_gamma_which_settles_and_releases_order_1(users, probe):
    i1 = STEP["order1"]["body"]["instructionId"]
    key = new_key("d1-order-2")
    body = expect(users["beta"].deliver(103, 100, 1_000_000, key), 201)
    assert body == {
        "operationId": body["operationId"], "type": "DELIVER_ORDER", "instructionId": body["instructionId"],
        "businessDate": STEP["d1"], "fromAccountId": 102, "toAccountId": 103, "quantity": 100,
        "amountCents": 1_000_000, "status": "SETTLED", "recycleReason": None,
        "settlementOperationId": body["settlementOperationId"],
        # beta's credit (+1,000,000) lets order 1 pass: -2,000,000 + 1,000,000 >= -1,500,000.
        # beta is order 1's receiver, so it may see the id.
        "releasedInstructionIds": [i1],
        "account": {"netCents": -1_000_000, "collateralValueCents": 10_450_000, "collateralMonitorCents": 9_450_000,
                    "netDebitCapCents": 1_500_000, "traditionalUnits": 1100},
    }
    assert type(body["settlementOperationId"]) is int
    assert probe.positions() == "101:800/0 102:1100/0 103:1100/0 104:0/0"
    assert {a: (r["net_cents"], r["collateral_value_cents"]) for a, r in accounts(probe).items()} == {
        101: (2_000_000, 7_600_000), 102: (-1_000_000, 10_450_000), 103: (-1_000_000, 10_450_000), 104: (0, 0),
    }
    # The settlement EVENT: the system acted (no actor), under the order's system key.
    dvp = sql_one(probe, "operation", operation_id=body["settlementOperationId"])
    assert {k: dvp[k] for k in dvp if k != "id"} == {
        "operation_type": "DVP_SETTLEMENT", "actor_user_id": None, "source_account_id": 102,
        "destination_account_id": 103, "asset_id": 1, "quantity": 100, "amount_cents": 1_000_000,
        "idempotency_key": f"dvp-{body['operationId']}", "request_hash": "system",
    }
    lines = probe.rows("ledger_lines_for_operation", operation_id=body["settlementOperationId"])
    assert [(r["account_id"], r["representation"], r["units_delta"]) for r in lines] == [
        (102, "TRADITIONAL", -100), (103, "TRADITIONAL", 100)]
    cash = sql_rows(probe, "cash_lines_for_operation", operation_id=body["settlementOperationId"])
    assert [(c["business_date"].isoformat(), c["ledger"], c["account_id"], c["amount_cents"]) for c in cash] == [
        (STEP["d1"], "PARTICIPANT_SETTLEMENT", 102, 1_000_000), (STEP["d1"], "PARTICIPANT_SETTLEMENT", 103, -1_000_000)]
    # Order 1 settled INSIDE beta's request, under its own settlement event (key dvp-<its order>).
    order1 = instruction(probe, i1)
    assert (order1["status"], order1["recycle_reason"]) == ("SETTLED", None)
    dvp1 = sql_one(probe, "operation", operation_id=order1["settlement_operation_id"])
    assert (dvp1["idempotency_key"], dvp1["source_account_id"], dvp1["destination_account_id"],
            dvp1["quantity"], dvp1["amount_cents"]) == (
        f"dvp-{STEP['order1']['body']['operationId']}", 101, 102, 200, 2_000_000)
    STEP["order2"] = {"key": key, "body": body}
    assert_books_balance(users)


def test_d1_after_step2_the_two_parties_see_order_1_settled(users):
    i1, i2 = STEP["order1"]["body"]["instructionId"], STEP["order2"]["body"]["instructionId"]
    assert seen_by(users, i1) == everyone_sees("SETTLED", alpha=None, beta=None, gamma=NOT_LISTED, exact=None)
    assert seen_by(users, i2) == everyone_sees("SETTLED", alpha=NOT_LISTED, beta=None, gamma=None, exact=None)
    row = next(r for r in expect(users["alpha"].get("/api/delivery-orders"), 200) if r["id"] == i1)
    assert row["settled_at"] is not None and type(row["settlement_operation_id"]) is int


def test_d1_settlement_accounts_participant_sees_its_own_overseers_see_all(users):
    alpha = [{
        "account_id": 101, "participant_name": "Participant Alpha (demo)", "settling_bank_id": 201,
        "bank_name": "Settling Bank One (demo)", "traditional_units": 800, "tokenized_units": 0,
        "net_cents": 2_000_000, "collateral_value_cents": 7_600_000, "collateral_monitor_cents": 9_600_000,
        "net_debit_cap_cents": 3_844_000,
        "headroom_cents": 5_844_000,  # min(CM 9,600,000, cap + net 5,844,000)
        "avg_top3_peak_cents": 3_200_000, "factor_bps": 12_500,
    }]
    assert expect(users["alpha"].get("/api/settlement/accounts"), 200) == alpha
    # A query parameter cannot widen a participant's view.
    assert expect(users["alpha"].get("/api/settlement/accounts", params={"account_id": 102}), 200) == alpha
    beta = expect(users["beta"].get("/api/settlement/accounts"), 200)
    # beta: CM 9,450,000 but only 500,000 left under its cap
    assert [(r["account_id"], r["collateral_monitor_cents"], r["headroom_cents"]) for r in beta] == [
        (102, 9_450_000, 500_000)]
    for overseer in ("auditor", "operator"):
        rows = expect(users[overseer].get("/api/settlement/accounts"), 200)
        assert [(r["account_id"], r["settling_bank_id"], r["net_cents"], r["headroom_cents"]) for r in rows] == [
            (101, 201, 2_000_000, 5_844_000), (102, 201, -1_000_000, 500_000),
            (103, 202, -1_000_000, 1_500_000), (104, 202, 0, 0),
        ]


def test_d1_step3_gamma_cannot_mint_995_but_can_mint_994(users, probe):
    before = snapshot(probe)
    key = new_key("d1-mint")
    refused = users["gamma"].mint(995, key)
    # (1,100 - 995) x 9,500 - 1,000,000 = -2,500: tokenized units have no collateral value
    assert (refused.status_code, refused.json()) == (422, {"error": MINT_BREACH})
    assert snapshot(probe) == before
    assert probe.rows_for_key(13, key) == 0
    body = expect(users["gamma"].mint(994, key), 201)  # the refused key was never saved
    assert body == {"operationId": body["operationId"], "type": "CONVERT_TO_TOKENIZED", "accountId": 103,
                    "quantity": 994, "traditionalUnits": 106, "tokenizedUnits": 994}
    gamma = accounts(probe)[103]
    # 106 x 9,500 - 1,000,000 = 7,000
    assert (gamma["collateral_value_cents"], gamma["net_cents"], gamma["collateral_monitor_cents"]) == (
        1_007_000, -1_000_000, 7_000)
    assert_books_balance(users)


def test_d1_step4_two_orders_recycle_one_on_gammas_cm_one_on_betas_cap(users, probe):
    alpha_after = {"netCents": 2_000_000, "collateralValueCents": 7_600_000, "collateralMonitorCents": 9_600_000,
                   "netDebitCapCents": 3_844_000, "traditionalUnits": 800}
    third = expect(users["alpha"].deliver(103, 100, 1_000_000, new_key("d1-order-3")), 202)
    # gamma's CM would be (106 + 100) x 9,500 - 2,000,000 = -43,000
    assert (third["status"], third["recycleReason"], third["releasedInstructionIds"], third["account"]) == (
        "RECYCLING", WAITING, [], alpha_after)
    fourth = expect(users["alpha"].deliver(102, 100, 1_000_000, new_key("d1-order-4")), 202)
    # beta's net would be -1,000,000 - 1,000,000 = -2,000,000 < -1,500,000
    assert (fourth["status"], fourth["recycleReason"], fourth["releasedInstructionIds"], fourth["account"]) == (
        "RECYCLING", WAITING, [], alpha_after)
    assert seen_by(users, third["instructionId"]) == everyone_sees(
        "RECYCLING", alpha=WAITING, beta=NOT_LISTED, gamma="RECEIVER_COLLATERAL_MONITOR",
        exact="RECEIVER_COLLATERAL_MONITOR")
    assert seen_by(users, fourth["instructionId"]) == everyone_sees(
        "RECYCLING", alpha=WAITING, beta="RECEIVER_NET_DEBIT_CAP", gamma=NOT_LISTED, exact="RECEIVER_NET_DEBIT_CAP")
    STEP["order3"], STEP["order4"] = third, fourth
    assert_books_balance(users)


def test_d1_step5_gammas_payment_releases_order_3_and_order_4_keeps_waiting(users, probe):
    key = new_key("d1-pay")
    body = expect(users["gamma"].pay(50_000, key), 201)
    assert body == {
        "operationId": body["operationId"], "type": "SETTLEMENT_PROGRESS_PAYMENT", "accountId": 103,
        "amountCents": 50_000, "businessDate": STEP["d1"],
        "releasedInstructionIds": [STEP["order3"]["instructionId"]],
        # net -950,000, then order 3: 206 x 9,500 - 1,950,000 = 7,000
        "account": {"netCents": -1_950_000, "collateralValueCents": 1_957_000, "collateralMonitorCents": 7_000,
                    "netDebitCapCents": 2_500_000, "traditionalUnits": 206},
    }
    op = sql_one(probe, "operation", operation_id=body["operationId"])
    assert (op["operation_type"], op["actor_user_id"], op["source_account_id"], op["destination_account_id"],
            op["asset_id"], op["quantity"], op["amount_cents"]) == (
        "SETTLEMENT_PROGRESS_PAYMENT", 13, None, 103, None, None, 50_000)
    cash = sql_rows(probe, "cash_lines_for_operation", operation_id=body["operationId"])
    assert [(c["ledger"], c["account_id"], c["settling_bank_id"], c["amount_cents"]) for c in cash] == [
        ("PARTICIPANT_SETTLEMENT", 103, None, 50_000), ("DTC_FED_ACCOUNT", None, None, -50_000)]
    assert instruction(probe, STEP["order3"]["instructionId"])["status"] == "SETTLED"
    order4 = instruction(probe, STEP["order4"]["instructionId"])
    assert (order4["status"], order4["recycle_reason"]) == ("RECYCLING", "RECEIVER_NET_DEBIT_CAP")
    assert probe.positions() == "101:700/0 102:1100/0 103:206/994 104:0/0"
    STEP["pay"] = {"key": key, "body": body}
    assert_books_balance(users)


def test_d1_replays_return_the_stored_answer_and_apply_nothing(users, probe):
    before = snapshot(probe)
    replay = users["alpha"].deliver(102, 200, 2_000_000, STEP["order1"]["key"])
    # The answer given THEN (RECYCLING), although order 1 has settled since: a replay repeats the
    # stored result; GET /api/delivery-orders is where the current status lives.
    assert (replay.status_code, replay.headers["idempotency-replayed"]) == (200, "true")
    assert replay.json() == STEP["order1"]["body"]
    replay = users["beta"].deliver(103, 100, 1_000_000, STEP["order2"]["key"])
    assert (replay.status_code, replay.json()) == (200, STEP["order2"]["body"])
    replay = users["gamma"].pay(50_000, STEP["pay"]["key"])
    assert (replay.status_code, replay.headers["idempotency-replayed"]) == (200, "true")
    assert replay.json() == STEP["pay"]["body"]
    assert snapshot(probe) == before  # gamma was not credited twice


def test_d1_a_key_reused_for_a_different_request_is_409(users, probe):
    before = snapshot(probe)
    order_key, pay_key = STEP["order1"]["key"], STEP["pay"]["key"]
    for response in (
        users["alpha"].deliver(102, 200, 2_000_001, order_key),  # another amount
        users["alpha"].deliver(103, 200, 2_000_000, order_key),  # another receiver
        users["alpha"].transfer(102, 1, order_key),  # another endpoint
        users["gamma"].pay(50_001, pay_key),
        users["gamma"].deliver(101, 1, 50_000, pay_key),
    ):
        assert (response.status_code, response.json()) == (409, REUSED)
    assert snapshot(probe) == before


def test_d1_final_settlement_before_the_cutoff_is_refused(users, probe):
    before = snapshot(probe)
    key = new_key("d1-early-final")
    response = users["operator"].final_settlement(key)
    assert (response.status_code, response.json()) == (422, {"error": "run the valued cutoff first"})
    assert snapshot(probe) == before
    assert probe.rows_for_key(15, key) == 0


def test_d1_step6_the_valued_cutoff_drops_order_4(users, probe):
    before = accounts(probe)
    key = new_key("d1-cutoff")
    body = expect(users["operator"].valued_cutoff(key), 201)
    i4 = STEP["order4"]["instructionId"]
    assert body == {"operationId": body["operationId"], "type": "VALUED_CUTOFF", "businessDate": STEP["d1"],
                    "droppedInstructionIds": [i4]}
    row = instruction(probe, i4)
    assert (row["status"], row["recycle_reason"], row["settlement_operation_id"]) == (
        "DROPPED_AT_CUTOFF", "RECEIVER_NET_DEBIT_CAP", None)  # the reason is kept
    assert seen_by(users, i4) == everyone_sees(
        "DROPPED_AT_CUTOFF", alpha=WAITING, beta="RECEIVER_NET_DEBIT_CAP", gamma=NOT_LISTED,
        exact="RECEIVER_NET_DEBIT_CAP")
    assert expect(users["gamma"].get("/api/settlement/day"), 200)["phase"] == "VALUED_CUTOFF_PASSED"
    assert accounts(probe) == before  # the cutoff moves no units and no money
    op = sql_one(probe, "operation", operation_id=body["operationId"])
    assert (op["operation_type"], op["actor_user_id"], op["source_account_id"], op["destination_account_id"],
            op["asset_id"], op["amount_cents"]) == ("VALUED_CUTOFF", 15, None, None, None, None)
    assert sql_rows(probe, "cash_lines_for_operation", operation_id=body["operationId"]) == []
    STEP["cutoff"] = {"key": key, "body": body}
    assert_books_balance(users)


def test_d1_after_the_cutoff_no_delivery_no_payment_no_second_cutoff(users, probe):
    before = snapshot(probe)
    keys = {11: new_key("late"), 13: new_key("late"), 15: new_key("late")}
    late_order = users["alpha"].deliver(102, 1, UNIT, keys[11])
    assert (late_order.status_code, late_order.json()) == (422, {"error": CLOSED})
    late_payment = users["gamma"].pay(1_000, keys[13])
    assert (late_payment.status_code, late_payment.json()) == (422, {"error": CLOSED})
    again = users["operator"].valued_cutoff(keys[15])
    assert (again.status_code, again.json()) == (422, {"error": "the valued cutoff has already run today"})
    assert snapshot(probe) == before
    assert [probe.rows_for_key(user_id, key) for user_id, key in keys.items()] == [0, 0, 0]
    # A replay is still a replay: the stored answer comes back before any phase check.
    replay = users["alpha"].deliver(102, 200, 2_000_000, STEP["order1"]["key"])
    assert (replay.status_code, replay.json()) == (200, STEP["order1"]["body"])
    replay = users["operator"].valued_cutoff(STEP["cutoff"]["key"])
    assert (replay.status_code, replay.headers["idempotency-replayed"]) == (200, "true")
    assert replay.json() == STEP["cutoff"]["body"]
    reused = users["operator"].final_settlement(STEP["cutoff"]["key"])  # same key, another step
    assert (reused.status_code, reused.json()) == (409, REUSED)
    assert snapshot(probe) == before


def test_d1_step6_final_settlement_figures_and_nss_file(users, probe):
    key = new_key("d1-final")
    body = expect(users["operator"].final_settlement(key), 201)
    STEP["d1_next"] = next_weekday(date.fromisoformat(STEP["d1"])).isoformat()
    assert body == {
        "operationId": body["operationId"], "type": "FINAL_SETTLEMENT", "businessDate": STEP["d1"],
        "nextBusinessDate": STEP["d1_next"],
        "figures": [
            {"accountId": 101, "finalNetCents": 3_000_000},  # credited by orders 1 and 3
            {"accountId": 102, "finalNetCents": -1_000_000},  # order 2 credit, order 1 debit
            {"accountId": 103, "finalNetCents": -1_950_000},  # orders 2 and 3, less its payment
            {"accountId": 104, "finalNetCents": 0},
        ],
        "nssFile": [  # one net-net figure per settling bank; they add up to the day's payment, 50,000
            {"settlingBankId": 201, "bankName": "Settling Bank One (demo)", "netNetCents": 2_000_000},
            {"settlingBankId": 202, "bankName": "Settling Bank Two (demo)", "netNetCents": -1_950_000},
        ],
    }
    STEP["final"] = {"key": key, "body": body}


def test_d1_closing_cash_lines_net_to_zero_every_net_is_0_and_the_next_weekday_opens(users, probe):
    final = STEP["final"]["body"]
    lines = sql_rows(probe, "cash_lines_for_operation", operation_id=final["operationId"])
    # each account -its net; each settling bank's Fed line +its net-net, naming the bank
    # (a NULL account or bank is shown as 0)
    assert sorted((c["ledger"], c["account_id"] or 0, c["settling_bank_id"] or 0, c["amount_cents"])
                  for c in lines) == sorted([
        ("PARTICIPANT_SETTLEMENT", 101, 0, -3_000_000), ("PARTICIPANT_SETTLEMENT", 102, 0, 1_000_000),
        ("PARTICIPANT_SETTLEMENT", 103, 0, 1_950_000),
        ("DTC_FED_ACCOUNT", 0, 201, 2_000_000), ("DTC_FED_ACCOUNT", 0, 202, -1_950_000),
    ])
    assert sum(c["amount_cents"] for c in lines) == 0
    assert {c["business_date"].isoformat() for c in lines} == {STEP["d1"]}
    assert nets(probe) == {101: 0, 102: 0, 103: 0, 104: 0}
    day = expect(users["beta"].get("/api/settlement/day"), 200)
    assert (day["business_date"], day["phase"]) == (STEP["d1_next"], "OPEN")
    assert date.fromisoformat(day["business_date"]).weekday() < 5
    result = assert_books_balance(users)
    assert next(r for r in result["closedDays"] if r["business_date"] == STEP["d1"]) == {
        "business_date": STEP["d1"], "figures_total_cents": 50_000, "nss_total_cents": 50_000,
        "payments_total_cents": 50_000, "fed_total_cents": 0, "accounts_mismatched": 0, "banks_mismatched": 0,
        "ok": True,
    }


def test_d1_final_settlement_replays_and_its_key_is_not_a_cutoff_key(users, probe):
    before = snapshot(probe)
    replay = users["operator"].final_settlement(STEP["final"]["key"])
    assert (replay.status_code, replay.headers["idempotency-replayed"]) == (200, "true")
    assert replay.json() == STEP["final"]["body"]
    reused = users["operator"].valued_cutoff(STEP["final"]["key"])
    assert (reused.status_code, reused.json()) == (409, REUSED)
    assert snapshot(probe) == before  # no cutoff ran on the new day


def test_d1_the_closed_day_is_read_by_auditor_and_operator_only(users):
    for overseer in ("auditor", "operator"):
        days = expect(users[overseer].get("/api/settlement/closed-days"), 200)
        dates = [d["business_date"] for d in days]
        assert dates[0] == STEP["d1"] and dates == sorted(dates, reverse=True)  # newest first
        newest = days[0]
        assert sorted((f["account_id"], f["participant_name"], f["final_net_cents"]) for f in newest["figures"]) == [
            (101, "Participant Alpha (demo)", 3_000_000), (102, "Participant Beta (demo)", -1_000_000),
            (103, "Participant Gamma (demo)", -1_950_000), (104, "Participant Delta (demo, NOT approved)", 0),
        ]
        assert sorted((n["settling_bank_id"], n["bank_name"], n["net_net_cents"]) for n in newest["nss_file"]) == [
            (201, "Settling Bank One (demo)", 2_000_000), (202, "Settling Bank Two (demo)", -1_950_000),
        ]
    refused = users["alpha"].get("/api/settlement/closed-days")
    assert (refused.status_code, refused.json()) == (403, {"error": "auditors and operators only"})


def test_d1_step7_units_are_still_3000_and_the_books_balance(users, probe):
    assert probe.positions() == "101:700/0 102:1100/0 103:206/994 104:0/0"
    assert assert_books_balance(users)["conservation"][0]["held_total"] == 3000


def test_d1_history_shows_the_cash_leg_to_the_parties_only(users, probe):
    dvp3 = instruction(probe, STEP["order3"]["instructionId"])["settlement_operation_id"]
    payment = STEP["pay"]["body"]["operationId"]
    alpha = {r["id"]: r for r in expect(users["alpha"].get("/api/operations"), 200)}
    assert (alpha[dvp3]["operation_type"], alpha[dvp3]["source_account_id"], alpha[dvp3]["destination_account_id"],
            alpha[dvp3]["quantity"], alpha[dvp3]["amount_cents"], alpha[dvp3]["actor_username"]) == (
        "DVP_SETTLEMENT", 101, 103, 100, 1_000_000, None)  # the system settled it: no actor
    assert payment not in alpha  # a payment names only the payer
    gamma = {r["id"]: r for r in expect(users["gamma"].get("/api/operations"), 200)}
    assert (gamma[payment]["operation_type"], gamma[payment]["symbol"], gamma[payment]["source_account_id"],
            gamma[payment]["destination_account_id"], gamma[payment]["quantity"], gamma[payment]["amount_cents"],
            gamma[payment]["actor_username"]) == (
        "SETTLEMENT_PROGRESS_PAYMENT", None, None, 103, None, 50_000, "gamma")  # no asset, no units
    day_steps = {STEP["cutoff"]["body"]["operationId"], STEP["final"]["body"]["operationId"]}
    operator = {r["id"]: r for r in expect(users["operator"].get("/api/operations"), 200)}
    assert {(operator[i]["operation_type"], operator[i]["actor_username"]) for i in day_steps} == {
        ("VALUED_CUTOFF", "operator"), ("FINAL_SETTLEMENT", "operator")}
    for name in ("alpha", "beta", "gamma"):  # the day's steps name no account
        assert not day_steps & {r["id"] for r in expect(users[name].get("/api/operations"), 200)}


# ---------- roles and bad requests (on the open, flat day 2) ----------

def test_wrong_roles_are_403_and_change_nothing(users, probe):
    before = snapshot(probe)
    participants_only, operators_only = {"error": "participants only"}, {"error": "operators only"}
    for response, error in (
        (users["auditor"].deliver(102, 1, UNIT, new_key("role")), participants_only),
        (users["operator"].deliver(102, 1, UNIT, new_key("role")), participants_only),
        (users["auditor"].pay(1_000, new_key("role")), participants_only),
        (users["operator"].pay(1_000, new_key("role")), participants_only),
        (users["alpha"].valued_cutoff(new_key("role")), operators_only),
        (users["auditor"].valued_cutoff(new_key("role")), operators_only),
        (users["beta"].final_settlement(new_key("role")), operators_only),
        (users["auditor"].final_settlement(new_key("role")), operators_only),
        (users["gamma"].get("/api/settlement/closed-days"), {"error": "auditors and operators only"}),
        (users["operator"].get("/api/reconciliation"), {"error": "auditors only"}),  # separation of duties
    ):
        assert (response.status_code, response.json()) == (403, error)
    assert snapshot(probe) == before


ORDERS, PAYMENTS = "/api/delivery-orders", "/api/settlement-progress-payments"
CUTOFF = "/api/settlement-day/valued-cutoff"
AMOUNT = "amountCents must be a whole number of cents 1..10000000000000"
QTY = "quantity must be a whole number 1..1000000"


def order(to=102, quantity=1, amount=UNIT, **extra):
    return {"toAccountId": to, "quantity": quantity, "amountCents": amount, "idempotencyKey": new_key("bad"), **extra}


BAD_REQUESTS = [
    # id, user, path, body, status, error
    ("deliver-to-yourself", "alpha", ORDERS, order(to=101), 400, "cannot deliver to yourself"),
    ("unknown-receiver", "alpha", ORDERS, order(to=99), 422, "unknown account"),
    ("amount-0", "alpha", ORDERS, order(amount=0), 400, AMOUNT),
    ("amount-negative", "alpha", ORDERS, order(amount=-100), 400, AMOUNT),
    ("amount-1.5", "alpha", ORDERS, order(amount=1.5), 400, AMOUNT),
    ("amount-100.0", "alpha", ORDERS, order(amount=100.0), 400, AMOUNT),
    ("amount-string", "alpha", ORDERS, order(amount="100"), 400, AMOUNT),
    ("amount-true", "alpha", ORDERS, order(amount=True), 400, AMOUNT),
    ("amount-over-100-billion-dollars", "alpha", ORDERS, order(amount=10_000_000_000_001), 400, AMOUNT),
    ("amount-missing", "alpha", ORDERS, {"toAccountId": 102, "quantity": 1, "idempotencyKey": "bad-x"}, 400, AMOUNT),
    ("quantity-0", "alpha", ORDERS, order(quantity=0), 400, QTY),
    ("deliverer-named-in-the-body", "alpha", ORDERS, order(fromAccountId=102), 400, "unexpected field: fromAccountId"),
    ("payment-amount-0", "beta", PAYMENTS, {"amountCents": 0, "idempotencyKey": "bad-p"}, 400, AMOUNT),
    ("payment-for-another-account", "beta", PAYMENTS, {"amountCents": 100, "idempotencyKey": "bad-p", "accountId": 103},
     400, "unexpected field: accountId"),
    ("cutoff-for-a-chosen-day", "operator", CUTOFF, {"idempotencyKey": "bad-c", "businessDate": "2026-10-19"},
     400, "unexpected field: businessDate"),
]


@pytest.mark.parametrize("case", BAD_REQUESTS, ids=[c[0] for c in BAD_REQUESTS])
def test_a_bad_request_is_refused_and_leaves_nothing(users, probe, case):
    _, user, path, payload, status, error = case
    before = snapshot(probe)
    response = users[user].post(path, payload)
    assert response.status_code == status, response.text
    assert response.json()["error"] == error
    if error == "unknown account":  # raised by the database: the reserve INSERT's foreign key
        assert response.json()["constraint"] == "operations_destination_account_id_fkey"
    assert snapshot(probe) == before


# ---------- Day 2: every recycle reason, the exact boundaries, privacy of releases ----------

def test_d2_the_next_day_starts_flat(probe):
    STEP["d2"] = assert_flat(probe)["business_date"].isoformat()
    assert STEP["d2"] == STEP["d1_next"]


def test_d2_a_deliverer_without_the_units_sees_its_own_reason(users, probe):
    body = expect(users["beta"].deliver(101, 1_000_000, 100, new_key("d2-short")), 202)
    # check 1: beta holds 1,100 traditional units, not 1,000,000 - a DELIVERER reason, so beta sees it
    assert (body["status"], body["recycleReason"], body["businessDate"]) == (
        "RECYCLING", "INSUFFICIENT_POSITION", STEP["d2"])
    assert body["account"] == figures_of(probe, 102)
    assert seen_by(users, body["instructionId"]) == everyone_sees(
        "RECYCLING", alpha=WAITING, beta="INSUFFICIENT_POSITION", gamma=NOT_LISTED, exact="INSUFFICIENT_POSITION")
    STEP["short"] = body["instructionId"]  # nobody ever holds 1,000,000 units: it waits until the cutoff


def test_d2_delta_has_a_zero_cap_so_a_delivery_to_it_always_recycles(users, probe):
    before = accounts(probe)
    body = expect(users["beta"].deliver(104, 1, 100, new_key("d2-delta")), 202)
    # Delta's net would be -100 < -0 (its $0 cap is a lab choice) - a receiver reason, masked for beta
    assert (body["status"], body["recycleReason"]) == ("RECYCLING", WAITING)
    assert instruction(probe, body["instructionId"])["recycle_reason"] == "RECEIVER_NET_DEBIT_CAP"
    assert accounts(probe) == before
    STEP["delta"] = body["instructionId"]  # Delta has no login: nothing can ever credit it


def test_d2_a_delivery_that_leaves_the_receivers_cm_at_exactly_0_settles(users, probe):
    set_traditional(users, probe, 103, 20)  # gamma: collateral 20 x 9,500 = 190,000, net 0
    gamma = accounts(probe)[103]
    assert (gamma["collateral_value_cents"], gamma["net_cents"]) == (20 * UNIT, 0)
    # gamma's CM after = 190,000 + 9,500 - 199,500 = 0, and 0 is allowed
    body = expect(users["beta"].deliver(103, 1, 21 * UNIT, new_key("d2-cm-zero")), 201)
    assert body["releasedInstructionIds"] == []  # beta's credit frees neither waiting order
    assert body["account"] == figures_of(probe, 102)
    assert accounts(probe)[103]["collateral_monitor_cents"] == 0
    assert [instruction(probe, i)["status"] for i in (STEP["short"], STEP["delta"])] == ["RECYCLING", "RECYCLING"]


def test_d2_one_cent_more_recycles_on_the_receivers_cm(users, probe):
    before = accounts(probe)
    # gamma's CM after = 0 + 9,500 - 9,501 = -1
    body = expect(users["beta"].deliver(103, 1, UNIT + 1, new_key("d2-cm-minus-1")), 202)
    assert (body["status"], body["recycleReason"]) == ("RECYCLING", WAITING)
    assert seen_by(users, body["instructionId"]) == everyone_sees(
        "RECYCLING", alpha=NOT_LISTED, beta=WAITING, gamma="RECEIVER_COLLATERAL_MONITOR",
        exact="RECEIVER_COLLATERAL_MONITOR")
    assert accounts(probe) == before
    STEP["cm"] = body["instructionId"]


def test_d2_at_cm_0_gamma_cannot_mint_even_one_unit(users, probe):
    before = snapshot(probe)
    response = users["gamma"].mint(1, new_key("d2-mint-1"))  # CM 0 - 9,500 = -9,500
    assert (response.status_code, response.json()) == (422, {"error": MINT_BREACH})
    assert snapshot(probe) == before


def test_d2_a_payment_by_the_receiver_releases_the_order_waiting_on_its_cm(users, probe):
    body = expect(users["gamma"].pay(3 * UNIT, new_key("d2-pay")), 201)
    # CM 0 + 28,500 = 28,500; the waiting order then costs gamma 9,501 - 9,500 = 1 cent: 28,499
    assert body["releasedInstructionIds"] == [STEP["cm"]]
    assert body["account"] == figures_of(probe, 103)
    assert body["account"]["collateralMonitorCents"] == 3 * UNIT - 1
    assert instruction(probe, STEP["cm"])["status"] == "SETTLED"


def test_d2_the_mint_gate_at_the_exact_boundary(users, probe):
    before = snapshot(probe)
    refused = users["gamma"].mint(3, new_key("d2-mint-3"))  # CM 28,499 - 3 x 9,500 = -1
    assert (refused.status_code, refused.json()) == (422, {"error": MINT_BREACH})
    assert snapshot(probe) == before
    one_cent = expect(users["gamma"].pay(1, new_key("d2-pay-1-cent")), 201)  # CM 28,500
    assert one_cent["releasedInstructionIds"] == []
    expect(users["gamma"].mint(3, new_key("d2-mint-3-again")), 201)  # CM exactly 0: allowed
    assert accounts(probe)[103]["collateral_monitor_cents"] == 0
    assert_books_balance(users)


def test_d2_a_deliverer_in_debit_recycles_on_its_own_cm(users, probe):
    # gamma (CM 0) gives away 9,500 of collateral for 1 cent: CM 0 - 9,500 + 1 = -9,499
    body = expect(users["gamma"].deliver(101, 1, 1, new_key("d2-deliverer-cm")), 202)
    assert (body["status"], body["recycleReason"]) == ("RECYCLING", "DELIVERER_COLLATERAL_MONITOR")  # its own side
    assert seen_by(users, body["instructionId"]) == everyone_sees(
        "RECYCLING", alpha=WAITING, beta=NOT_LISTED, gamma="DELIVERER_COLLATERAL_MONITOR",
        exact="DELIVERER_COLLATERAL_MONITOR")
    STEP["deliverer_cm"] = body["instructionId"]


def test_d2_a_release_caused_by_a_third_party_is_hidden_from_it(users, probe):
    set_traditional(users, probe, 101, 10)  # alpha: 10 traditional units (its net is 0)
    x = expect(users["alpha"].deliver(102, 15, 60_000, new_key("d2-x")), 202)
    assert x["recycleReason"] == "INSUFFICIENT_POSITION"  # 10 < 15
    STEP["x"] = x["instructionId"]
    # gamma delivers 5 units to alpha (gamma's CM after: 0 - 47,500 + 60,000 = 12,500). gamma's credit
    # starts the retry: alpha now holds 15, so X (alpha -> beta) settles, with alpha's CM at exactly
    # 0 - and then gamma's own waiting order passes too (12,500 - 9,500 + 1 = 3,001).
    body = expect(users["gamma"].deliver(101, 5, 60_000, new_key("d2-release")), 201)
    # Both settled inside gamma's request, but gamma is not a party to X: it may learn only its own.
    assert body["releasedInstructionIds"] == [STEP["deliverer_cm"]]
    assert body["account"] == figures_of(probe, 103)
    assert [instruction(probe, i)["status"] for i in (STEP["x"], STEP["deliverer_cm"])] == ["SETTLED", "SETTLED"]
    assert seen_by(users, STEP["x"]) == everyone_sees("SETTLED", alpha=None, beta=None, gamma=NOT_LISTED, exact=None)
    assert [instruction(probe, i)["status"] for i in (STEP["short"], STEP["delta"])] == ["RECYCLING", "RECYCLING"]
    assert probe.positions() == "101:1/690 102:1113/0 103:13/1183 104:0/0"
    assert_books_balance(users)


def test_d2_the_cutoff_drops_the_two_orders_that_could_never_settle(users, probe):
    body = expect(users["operator"].valued_cutoff(new_key("d2-cutoff")), 201)
    assert body["droppedInstructionIds"] == sorted([STEP["short"], STEP["delta"]])
    assert sql_one(probe, "day_status")["recycling"] == 0
    assert seen_by(users, STEP["short"]) == everyone_sees(
        "DROPPED_AT_CUTOFF", alpha=WAITING, beta="INSUFFICIENT_POSITION", gamma=NOT_LISTED,
        exact="INSUFFICIENT_POSITION")


def test_d2_final_settlement_turns_the_nets_into_figures_and_net_net_lines(users, probe):
    before = nets(probe)
    # alpha -60,000 + 60,000 - 1; beta +199,500 + 9,501 - 60,000; gamma the rest plus its payments
    assert before == {101: -1, 102: 149_001, 103: -120_499, 104: 0}
    assert sum(before.values()) == 3 * UNIT + 1  # DVP only moves money between participants: all nets = payments
    body = expect(users["operator"].final_settlement(new_key("d2-final")), 201)
    assert body["figures"] == [{"accountId": a, "finalNetCents": n} for a, n in sorted(before.items())]
    assert body["nssFile"] == [
        {"settlingBankId": 201, "bankName": "Settling Bank One (demo)", "netNetCents": before[101] + before[102]},
        {"settlingBankId": 202, "bankName": "Settling Bank Two (demo)", "netNetCents": before[103] + before[104]},
    ]
    lines = sql_rows(probe, "cash_lines_for_operation", operation_id=body["operationId"])
    expected = [("PARTICIPANT_SETTLEMENT", a, 0, -n) for a, n in before.items() if n]
    expected += [("DTC_FED_ACCOUNT", 0, line["settlingBankId"], line["netNetCents"])
                 for line in body["nssFile"] if line["netNetCents"]]
    assert sorted((c["ledger"], c["account_id"] or 0, c["settling_bank_id"] or 0, c["amount_cents"])
                  for c in lines) == sorted(expected)
    assert sum(c["amount_cents"] for c in lines) == 0
    assert body["nextBusinessDate"] == next_weekday(date.fromisoformat(STEP["d2"])).isoformat()
    status = assert_flat(probe)
    assert status["business_date"].isoformat() == body["nextBusinessDate"]
    result = assert_books_balance(users)
    assert result["conservation"][0]["held_total"] == 3000


# ---------- Day 3: the retry itself ----------
# Each step starts from a flat day (start_flat closes the one before) and sets up its own positions.

def test_d3_a_waiting_orders_reason_is_kept_current_when_a_retry_cannot_settle_it(users, probe):
    start_flat(users, probe)
    set_traditional(users, probe, 102, 1000)  # beta: collateral 9,500,000, net 0
    ensure_traditional(users, probe, 101, 1)
    ensure_traditional(users, probe, 103, 100)  # gamma's CM can take beta's 1,000,000 delivery below
    # alpha -> beta, 1 unit for 2,000,000: beta's net would be -2,000,000 < -1,500,000 (check 4)
    order = expect(users["alpha"].deliver(102, 1, 2_000_000, new_key("d3-reason")), 202)
    assert instruction(probe, order["instructionId"])["recycle_reason"] == "RECEIVER_NET_DEBIT_CAP"
    # beta mints 990 (its net is 0, so the mint is allowed): 10 traditional units, collateral 95,000
    expect(users["beta"].mint(990, new_key("d3-mint")), 201)
    # beta delivers its 10 units to gamma for 1,000,000: it settles, and beta's credit starts a retry.
    # The order now fits beta's cap (1,000,000 - 2,000,000 = -1,000,000 >= -1,500,000) but not beta's CM
    # any more: (0 + 9,500) + (1,000,000 - 2,000,000) = -990,500 (check 3). It keeps waiting, under the
    # reason that holds NOW - the one beta sees.
    with spying() as spy:
        body = expect(users["beta"].deliver(103, 10, 1_000_000, new_key("d3-credit")), 201)
    assert body["releasedInstructionIds"] == []
    assert spy.seen.count("update_recycle_reason") == 1
    row = instruction(probe, order["instructionId"])
    assert (row["status"], row["recycle_reason"]) == ("RECYCLING", "RECEIVER_COLLATERAL_MONITOR")
    assert seen_by(users, order["instructionId"]) == everyone_sees(
        "RECYCLING", alpha=WAITING, beta="RECEIVER_COLLATERAL_MONITOR", gamma=NOT_LISTED,
        exact="RECEIVER_COLLATERAL_MONITOR")
    # A retry that finds the same reason again writes nothing.
    with spying() as spy:
        paid = expect(users["gamma"].pay(1, new_key("d3-same-reason")), 201)
    assert paid["releasedInstructionIds"] == []
    assert "list_recycling_instructions" in spy.seen and "update_recycle_reason" not in spy.seen
    assert_books_balance(users)


def test_d3_a_retry_settles_the_largest_amount_first_then_the_oldest(users, probe):
    start_flat(users, probe)
    set_traditional(users, probe, 102, 400)  # beta: collateral 3,800,000 - its CM is never the limit here
    for deliverer in (101, 103):
        ensure_traditional(users, probe, deliverer, 5)
    expect(users["gamma"].deliver(102, 1, 1_000_000, new_key("d3-to-minus-1m")), 201)  # beta's net -1,000,000
    # Two orders that need the same room under beta's 1,500,000 cap; the OLDER one is the smaller.
    o1 = expect(users["alpha"].deliver(102, 1, 600_000, new_key("d3-o1")), 202)  # -1,600,000 < -1,500,000
    o2 = expect(users["gamma"].deliver(102, 1, 900_000, new_key("d3-o2")), 202)  # -1,900,000
    for waiting in (o1, o2):
        assert instruction(probe, waiting["instructionId"])["recycle_reason"] == "RECEIVER_NET_DEBIT_CAP"
    # beta pays 500,000: net -500,000, room for ONE of them. The retry takes the larger amount first
    # (o2: -1,400,000); then o1 no longer fits (-2,000,000). Oldest first, o1 would have won instead.
    paid = expect(users["beta"].pay(500_000, new_key("d3-room")), 201)
    assert paid["releasedInstructionIds"] == [o2["instructionId"]]
    # -1,400,000: the engine re-read beta after settling o2, so o1 was judged on the new net (on the
    # stale -500,000 it would have "passed", and the CHECK backstop would have refused the request).
    assert paid["account"]["netCents"] == -1_400_000
    row = instruction(probe, o1["instructionId"])
    assert (row["status"], row["recycle_reason"]) == ("RECYCLING", "RECEIVER_NET_DEBIT_CAP")
    # Equal amounts: the older order first. o3 needs 600,000 too; the next 500,000 makes room for one.
    o3 = expect(users["gamma"].deliver(102, 1, 600_000, new_key("d3-o3")), 202)
    paid = expect(users["beta"].pay(500_000, new_key("d3-tie")), 201)  # net -900,000
    assert paid["releasedInstructionIds"] == [o1["instructionId"]]  # -1,500,000: exactly the cap
    assert instruction(probe, o3["instructionId"])["status"] == "RECYCLING"
    assert accounts(probe)[102]["net_cents"] == -1_500_000
    assert_books_balance(users)


def test_d3_a_payment_releases_a_chain_that_needs_a_second_pass(users, probe):
    start_flat(users, probe)
    set_traditional(users, probe, 101, 10)  # alpha: collateral 95,000, net 0
    set_traditional(users, probe, 103, 5)  # gamma: collateral 47,500, net 0
    ensure_traditional(users, probe, 102, 1)
    # beta delivers 1 unit to gamma for 57,000: gamma's CM after = 47,500 + 9,500 - 57,000 = exactly 0
    expect(users["beta"].deliver(103, 1, 57_000, new_key("d3-gamma-cm-0")), 201)
    assert accounts(probe)[103]["collateral_monitor_cents"] == 0
    # X: alpha -> beta, 15 units for 200 - alpha holds only 10 (check 1)
    x = expect(users["alpha"].deliver(102, 15, 200, new_key("d3-x")), 202)
    assert x["recycleReason"] == "INSUFFICIENT_POSITION"
    # G: gamma -> alpha, 5 units for 100 - gamma's CM after = 57,000 - 47,500 - 57,000 + 100 = -47,400 (check 2)
    g = expect(users["gamma"].deliver(101, 5, 100, new_key("d3-g")), 202)
    assert g["recycleReason"] == "DELIVERER_COLLATERAL_MONITOR"
    # gamma pays 50,000 (net -7,000). Pass 1, largest amount first: X still fails (10 < 15), then G
    # settles (gamma's CM after = 9,500 - 7,000 + 100 = 2,600) and alpha now holds 15. Pass 2: X settles
    # (alpha's CM after = 142,500 - 142,500 - 100 + 200 = 100). Pass 3 settles nothing.
    paid = expect(users["gamma"].pay(50_000, new_key("d3-chain")), 201)
    # Both settled inside gamma's request, but gamma is no party to X: the payer learns only about G.
    assert paid["releasedInstructionIds"] == [g["instructionId"]]
    assert [instruction(probe, i)["status"] for i in (x["instructionId"], g["instructionId"])] == [
        "SETTLED", "SETTLED"]
    assert seen_by(users, x["instructionId"]) == everyone_sees(
        "SETTLED", alpha=None, beta=None, gamma=NOT_LISTED, exact=None)
    assert {a: r["collateral_monitor_cents"] for a, r in accounts(probe).items() if a in (101, 103)} == {
        101: 100, 103: 2_600}
    assert_books_balance(users)


def test_d3_the_largest_amount_the_api_accepts(users, probe):
    """amountCents 1..10,000,000,000,000: the upper edge itself is valid (BAD_REQUESTS refuses one cent more)."""
    largest = 10_000_000_000_000
    ensure_traditional(users, probe, 101, 1)
    before = accounts(probe)
    order = expect(users["alpha"].deliver(104, 1, largest, new_key("d3-max")), 202)  # validated, not a 400
    # Delta's CM would be 9,500 - largest: a receiver reason, masked for alpha
    assert (order["status"], order["recycleReason"], order["amountCents"]) == ("RECYCLING", WAITING, largest)
    assert accounts(probe) == before
    paid = expect(users["beta"].pay(largest, new_key("d3-max-pay")), 201)
    assert paid["account"]["netCents"] == before[102]["net_cents"] + largest
    assert_books_balance(users)


def test_d3_closes_flat_and_the_books_balance(users, probe):
    final = close_the_day(users)
    assert sum(f["finalNetCents"] for f in final["figures"]) == sum(n["netNetCents"] for n in final["nssFile"])
    assert_flat(probe)
    assert_books_balance(users)


# ---------- the next business day: the weekday rule on every kind of day ----------
# Rolled back, on a connection of its own: the sequencer row is put on a chosen date, whatever day
# the suite runs on, and final_settlement.sql's open_next_business_day runs on it.

@pytest.mark.parametrize("closing,opening", [
    (date(2026, 10, 23), date(2026, 10, 26)),  # Friday -> Monday
    (date(2026, 10, 24), date(2026, 10, 26)),  # Saturday -> Monday
    (date(2026, 10, 25), date(2026, 10, 26)),  # Sunday -> Monday
    (date(2026, 10, 21), date(2026, 10, 22)),  # Wednesday -> Thursday
    (date(2026, 10, 26), date(2026, 10, 27)),  # Monday -> Tuesday
], ids=["fri", "sat", "sun", "wed", "mon"])
def test_the_next_business_day_is_the_next_weekday(database_url, closing, opening):
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            moved = run(conn, SETTLEMENT_PROBES["set_settlement_day"],
                        {"business_date": closing, "phase": "VALUED_CUTOFF_PASSED"})
            assert moved.rowcount == 1
            assert run(conn, FINAL_SETTLEMENT["open_next_business_day"]).fetchall() == [{"business_date": opening}]
            assert run(conn, SETTLEMENT_PROBES["day_status"]).fetchone()["phase"] == "OPEN"
    assert opening == next_weekday(closing)  # the tests' own helper agrees with the SQL


def test_the_next_business_day_opens_only_after_the_valued_cutoff(database_url):
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            run(conn, SETTLEMENT_PROBES["set_settlement_day"], {"business_date": date(2026, 10, 21), "phase": "OPEN"})
            assert run(conn, FINAL_SETTLEMENT["open_next_business_day"]).rowcount == 0  # its WHERE guard

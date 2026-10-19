"""Lesson 9: one snapshot for both checks, and both checks catch the corruption they are meant to.
Lesson 14: the six settlement checks run in the same snapshot, and each one catches its corruption.

The unit corruptions (A-E) and their undo statements are in tests/sql/fixtures.sql; the settlement
corruptions (S1-S15) and the missing-row drills (M1-M2) are in tests/sql/settlement_fixtures.sql.
The settlement drills need a settled deliver order and a closed day, so their fixture closes one day
through the API first; the module leaves the day OPEN with every net at 0.
"""

import psycopg
import pytest

from app import queries
from app.domain import reconciliation
from app.sql import load_sql_blocks
from tests.helpers import FIXTURES, PROBES, TESTS_DIR, new_key, run, set_tokenized
from tests.safety import SERVER_DIR
from tests.spy import spying
from tests.test_90_settlement import (
    SETTLEMENT_CHECKS,
    UNIT,
    assert_books_balance,
    assert_flat,
    close_the_day,
    ensure_traditional,
    expect,
    failing_checks,
    start_flat,
)

SETTLEMENT_FIXTURES = load_sql_blocks(TESTS_DIR / "sql" / "settlement_fixtures.sql")
RECON = queries.load("reconciliation", queries.RECONCILIATION_BLOCKS)


def report(users):
    response = users["auditor"].get("/api/reconciliation")
    assert response.status_code == 200
    return response.json()


def bucket(result, account_id, representation):
    return next(r for r in result["positionsVsLedger"]
                if r["account_id"] == account_id and r["representation"] == representation)


def tokenized_in_check_2(result):
    return sum(r["stored_units"] for r in result["positionsVsLedger"] if r["representation"] == "TOKENIZED")


def test_g_a_transfer_committed_between_the_two_checks_is_not_seen(users, probe):
    set_tokenized(users, probe, 101, 50)
    middle = {}

    def transfer_between_checks(conn):  # runs inside the report's request, after check (1)
        middle["response"] = users["alpha"].fork().transfer(102, 5, new_key("G"))

    with spying(after={"check_conservation": transfer_between_checks}):
        result = report(users)
    assert middle["response"].status_code == 201  # committed while the report was running
    row = bucket(result, 101, "TOKENIZED")
    assert (row["stored_units"], row["ledger_units"]) == (50, 50)  # check (2) still sees the snapshot
    assert result["passed"] is True
    assert probe.tokenized(101) == 45


def test_g_contrast_without_the_snapshot_the_two_checks_disagree(users, probe):
    def mint_between_checks(conn):
        assert users["alpha"].fork().mint(5, new_key("Gc")).status_code == 201

    with spying(after={"check_conservation": mint_between_checks}):
        snapshot = report(users)
    assert tokenized_in_check_2(snapshot) == snapshot["conservation"][0]["tokenized_total"]

    # The same report with set_snapshot swapped for a no-op: plain READ COMMITTED.
    with spying(after={"check_conservation": mint_between_checks}, replace={"set_snapshot": PROBES["no_op"]}):
        no_snapshot = report(users)
    assert tokenized_in_check_2(no_snapshot) == no_snapshot["conservation"][0]["tokenized_total"] + 5


def test_the_file_runs_as_one_transaction_like_psql_dash_1(database_url):
    # utf-8-sig, like the runner and app.sql: a file saved with a BOM (Windows editors) still works.
    text = (SERVER_DIR / "db" / "queries" / "reconciliation.sql").read_text(encoding="utf-8-sig")
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            cur = conn.execute(text)  # every statement at once, like piping the file
            results = []
            while True:
                if cur.description:
                    ok = [column.name for column in cur.description].index("ok")
                    results.append([row[ok] for row in cur.fetchall()])
                else:
                    results.append(None)
                if not cur.nextset():
                    break
    assert results[0] is None  # SET TRANSACTION
    assert len(results) == 1 + 2 + len(SETTLEMENT_CHECKS)  # the snapshot, two unit checks, six settlement checks
    assert all(ok is True for check in results[1:] for ok in check)  # every "ok" column of every check


CORRUPTIONS = [
    # name, corrupt blocks, undo blocks, check (1) ok?, buckets check (2) flags
    ("A-move-without-ledger", ["corrupt_a_move_without_ledger"], ["undo_a"], True,
     {(102, "TOKENIZED"), (102, "TRADITIONAL")}),
    ("B-create-from-nothing", ["corrupt_b_create_from_nothing"], ["undo_b"], False, {(103, "TOKENIZED")}),
    ("C-stray-ledger-line", ["corrupt_c_stray_ledger_line"], ["undo_c"], True, {(104, "TOKENIZED")}),
    ("D-consistent-inflation", ["corrupt_d_position", "corrupt_d_ledger"], ["undo_d_ledger", "undo_d_position"],
     False, set()),
    ("E-custody-figure", ["corrupt_e_custody_figure"], ["undo_e"], False, set()),
]


@pytest.mark.parametrize("case", CORRUPTIONS, ids=[c[0] for c in CORRUPTIONS])
def test_reconciliation_catches_corruption(users, probe, case):
    name, corrupt, undo, check_1_ok, flagged = case
    assert report(users)["passed"] is True
    for block in corrupt:
        probe.run_block(FIXTURES[block])
    try:
        result = report(users)
    finally:
        for block in undo:
            probe.run_block(FIXTURES[block])
    assert result["passed"] is False
    conservation = result["conservation"][0]
    assert conservation["ok"] is check_1_ok
    assert {(r["account_id"], r["representation"]) for r in result["positionsVsLedger"] if not r["ok"]} == flagged
    if name.startswith("B") or name.startswith("D"):
        assert conservation["held_total"] == 3007
    if name.startswith("C"):
        delta = bucket(result, 104, "TOKENIZED")
        assert (delta["stored_units"], delta["ledger_units"]) == (0, 4)
    if name.startswith("E"):
        assert (conservation["total_custodied_units"], conservation["held_total"]) == (2999, 3000)
    assert report(users)["passed"] is True  # undone


# ---------- Lesson 14: the settlement checks ----------

WRONG_ACCOUNT = 103  # gamma: no party to the settled order below (alpha 101 -> beta 102)


@pytest.fixture(scope="module")
def closed_day(users, probe):
    """One settlement day closed through the API: alpha's deliver order to beta (2 units for $190.00)
    settles, beta pays $10.00 in and gamma $5.00, the operator runs the valued cutoff and final
    settlement - so both settling banks get a non-zero NSS line and a closing Fed line (drill S13).
    Then alpha gets 40 tokenized units (drill S4). The day after is OPEN with every net at 0."""
    start_flat(users, probe)
    ensure_traditional(users, probe, 103, 100)  # drill S12 debits gamma $190.00: its CM must cover it
    ensure_traditional(users, probe, 101, 2)
    order = expect(users["alpha"].deliver(102, 2, 2 * UNIT, new_key("S-order")), 201)
    expect(users["beta"].pay(1_000, new_key("S-pay")), 201)
    expect(users["gamma"].pay(500, new_key("S-pay")), 201)
    final = close_the_day(users)
    assert final["nssFile"] == [  # alpha +19,000 and beta -18,000; gamma +500 and Delta 0
        {"settlingBankId": 201, "bankName": "Settling Bank One (demo)", "netNetCents": 1_000},
        {"settlingBankId": 202, "bankName": "Settling Bank Two (demo)", "netNetCents": 500},
    ]
    assert_flat(probe)
    set_tokenized(users, probe, 101, 40)
    assert_books_balance(users)
    return {"instruction_id": order["instructionId"], "business_date": final["businessDate"]}


def flagged_accounts(result, check):
    return {row["account_id"] for row in result[check] if row["ok"] is not True}


SETTLEMENT_CORRUPTIONS = [
    # name, corrupt blocks, undo blocks (in reverse order), the ONE check that must fail
    ("S1-unbalanced-cash-line", ["corrupt_s1_unbalanced_cash_line"], ["undo_s1"], "cashLinesNetToZero"),
    ("S2-net-without-cash-line", ["corrupt_s2_net_without_cash_line"], ["undo_s2"], "settlementVsCashLedger"),
    ("S3-collateral-without-units", ["corrupt_s3_collateral_without_units"], ["undo_s3"], "collateralVsPositions"),
    ("S4-tokenized-counted-as-collateral", ["corrupt_s4_tokenized_counted_as_collateral"], ["undo_s4"],
     "collateralVsPositions"),
    ("S5-units-to-the-wrong-account-written-consistently",
     ["corrupt_s5_ledger_line", "corrupt_s5_positions", "corrupt_s5_collateral"],
     ["undo_s5_collateral", "undo_s5_positions", "undo_s5_ledger_line"], "settledInstructionsVsLedgers"),
    ("S6-amount-changed-after-settlement", ["corrupt_s6_amount_changed_after_settlement"], ["undo_s6"],
     "settledInstructionsVsLedgers"),
    ("S7-orphan-settlement", ["corrupt_s7_orphan_settlement"], ["undo_s7"], "settledInstructionsVsLedgers"),
    ("S11-operation-names-the-wrong-receiver", ["corrupt_s11_operation_names_the_wrong_receiver"], ["undo_s11"],
     "settledInstructionsVsLedgers"),
    ("S12-money-to-the-wrong-account-written-consistently", ["corrupt_s12_cash_line", "corrupt_s12_nets"],
     ["undo_s12_nets", "undo_s12_cash_line"], "settledInstructionsVsLedgers"),
    ("S8-cap-typed-by-hand", ["corrupt_s8_cap_typed_by_hand"], ["undo_s8"], "riskControls"),
    ("S9-maximum-cap-lowered", ["corrupt_s9_maximum_cap_lowered"], ["undo_s9"], "riskControls"),
    ("S10-closed-day-figure-changed", ["corrupt_s10_closed_day_figure_changed"], ["undo_s10"], "closedDays"),
    ("S13-nss-and-fed-lines-shifted-between-banks",
     ["corrupt_s13_nss_lines_shifted_between_banks", "corrupt_s13_fed_lines_shifted_between_banks"],
     ["undo_s13_fed_lines", "undo_s13_nss_lines"], "closedDays"),
    ("S14-nss-file-and-figures-above-the-payments", ["corrupt_s14_nss_line_raised", "corrupt_s14_figure_raised"],
     ["undo_s14_figure", "undo_s14_nss_line"], "closedDays"),
    ("S15-fed-account-not-flat-at-the-end-of-the-day", ["corrupt_s15_payment_fed_line_moved_to_the_open_day"],
     ["undo_s15"], "closedDays"),
]


def closed_day_row(result, business_date):
    return next(r for r in result["closedDays"] if r["business_date"] == business_date)


@pytest.mark.parametrize("case", SETTLEMENT_CORRUPTIONS, ids=[c[0] for c in SETTLEMENT_CORRUPTIONS])
def test_reconciliation_catches_settlement_corruption(users, probe, closed_day, case):
    name, corrupt, undo, check = case
    params = {"instruction_id": closed_day["instruction_id"], "wrong_account_id": WRONG_ACCOUNT}
    assert report(users)["passed"] is True
    tokenized_alpha = probe.tokenized(101)
    for block in corrupt:
        assert run(probe.conn, SETTLEMENT_FIXTURES[block], params).rowcount >= 1, block
    try:
        result = report(users)
    finally:
        for block in undo:
            assert run(probe.conn, SETTLEMENT_FIXTURES[block], params).rowcount >= 1, block
    assert result["passed"] is False
    assert failing_checks(result) == {check}  # exactly the check that guards it, nothing else
    code = name.split("-")[0]
    if code == "S1":
        assert result["cashLinesNetToZero"][0]["failing"] == 1
    if code == "S2":
        assert flagged_accounts(result, check) == {103}
        row = next(r for r in result[check] if r["account_id"] == 103)
        assert row["stored_net_cents"] - row["ledger_net_cents"] == 100
    if code == "S3":
        assert flagged_accounts(result, check) == {102}
    if code == "S4":  # tokenized units must count zero (the SEC staff no-action letter's rule)
        assert flagged_accounts(result, check) == {101}
        row = next(r for r in result[check] if r["account_id"] == 101)
        assert row["stored_collateral_cents"] - row["expected_collateral_cents"] == tokenized_alpha * UNIT
    if code in ("S5", "S6", "S11", "S12"):  # S5, S12: every ledger balances, only the instruction disagrees
        summary = result[check][0]
        assert (summary["failing"], summary["orphan_settlements"]) == (1, 0)
    if code == "S7":
        summary = result[check][0]
        assert (summary["failing"], summary["orphan_settlements"]) == (0, 1)
    if code == "S8":
        assert flagged_accounts(result, check) == {102}
        row = next(r for r in result[check] if r["account_id"] == 102)
        assert row["net_debit_cap_cents"] - row["expected_cap_cents"] == 100_000
    if code == "S9":  # only alpha's cap sits at the maximum
        assert flagged_accounts(result, check) == {101}
    if check == "closedDays":  # S10, S13, S14, S15: the closed day, and only it
        assert {r["business_date"] for r in result[check] if not r["ok"]} == {closed_day["business_date"]}
        row = closed_day_row(result, closed_day["business_date"])
        totals = (row["figures_total_cents"], row["nss_total_cents"], row["payments_total_cents"],
                  row["fed_total_cents"])
        mismatched = (row["accounts_mismatched"], row["banks_mismatched"])
    if code == "S10":  # alpha's figure: the totals disagree, and so do alpha's line and Bank One's line
        assert (totals, mismatched) == ((1_501, 1_500, 1_500, 0), (1, 1))
    if code == "S13":  # every total agrees: only the bank-by-bank comparison sees it
        assert (totals, mismatched) == ((1_500, 1_500, 1_500, 0), (0, 2))
    if code == "S14":  # figures = NSS file, but both say 1 cent more than was paid in
        assert totals[1] - totals[2] == 1 and totals[0] == totals[1]
    if code == "S15":  # the Fed account does not end the day at 0; everything else agrees
        assert totals[3] != 0 and totals[0] == totals[1] == totals[2] and mismatched == (0, 0)
    assert report(users)["passed"] is True  # undone


MISSING_ROWS = [
    # name, blocks (one transaction, rolled back), the checks that must fail, the accounts they flag
    ("M1-no-settlement-day-row", ["missing_settlement_day"], {"riskControls"}, {101, 102, 103, 104}),
    ("M2-delta-without-a-settlement-account", ["missing_figures_of_delta", "missing_settlement_account_of_delta"],
     {"settlementVsCashLedger", "collateralVsPositions", "riskControls"}, {104}),
]


def settlement_checks_on(conn):
    """The six settlement checks on `conn`, inside the caller's transaction, so they see its own
    uncommitted changes (without set_snapshot, which must be the first statement of a transaction)."""
    return {key: run(conn, RECON[block]).fetchall() for key, block in reconciliation.SETTLEMENT_CHECKS}


@pytest.mark.parametrize("case", MISSING_ROWS, ids=[c[0] for c in MISSING_ROWS])
def test_a_missing_row_fails_the_report_instead_of_passing_unseen(database_url, closed_day, case):
    """A check that starts FROM the rows it checks has no failing row when a row is missing. The
    settlement day and every account's settlement account must exist: without them the engine
    answers 500, so the report must fail too."""
    _, blocks, checks, flagged = case
    with psycopg.connect(database_url) as conn:
        with conn.transaction(force_rollback=True):
            assert failing_checks(settlement_checks_on(conn)) == set()
            for block in blocks:
                assert run(conn, SETTLEMENT_FIXTURES[block]).rowcount >= 1, block
            result = settlement_checks_on(conn)
    assert failing_checks(result) == checks
    for check in checks:
        rows = [row for row in result[check] if row["ok"] is not True]
        assert {row["account_id"] for row in rows} == flagged, check
        assert all(row["ok"] is False for row in rows), check  # FALSE computed in SQL, never NULL


def test_the_module_leaves_the_day_open_and_flat(users, probe, closed_day):
    assert_flat(probe)
    assert_books_balance(users)

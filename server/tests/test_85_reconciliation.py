"""Lesson 9: one snapshot for both checks, and both checks catch the corruption they are meant to.

The corruptions (A-E) and their undo statements are in tests/sql/fixtures.sql.
"""

import psycopg
import pytest

from tests.helpers import FIXTURES, PROBES, new_key, set_tokenized
from tests.safety import SERVER_DIR
from tests.spy import spying


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
            cur = conn.execute(text)  # all three statements at once, like piping the file
            results = []
            while True:
                results.append(cur.fetchall() if cur.description else None)
                if not cur.nextset():
                    break
    assert results[0] is None  # SET TRANSACTION
    assert all(row[-1] is True for row in results[1] + results[2])  # every "ok" column


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

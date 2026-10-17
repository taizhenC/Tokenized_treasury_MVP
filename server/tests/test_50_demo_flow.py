"""Lessons 6-10: the acceptance sequence on a FRESHLY SEEDED database, through the real HTTP API.

mint 100 -> transfer 25 -> burn 10 -> reverse transfer 5, then history, reconciliation,
idempotent replay / 409, and every rejection with nothing persisted.
The steps share state and run in file order, like the lesson sequence they mirror.
"""

from datetime import datetime

import psycopg
import pytest
from psycopg import errors as pg

from app.domain import transfers
from app.errors import classify_db_error
from tests.helpers import SEEDED_POSITIONS

STEP: dict[str, dict] = {}  # response bodies of the steps, by name


@pytest.fixture(scope="module", autouse=True)
def freshly_seeded(probe):
    if probe.positions() != SEEDED_POSITIONS or probe.one("counts")["operations"] != 3:
        pytest.fail("test_50 needs a freshly seeded database: run `python -m tests.reset_db` first", pytrace=False)


# ---------- Lessons 6-7: the happy path ----------

def test_l6_alpha_mints_100(users, probe):
    response = users["alpha"].mint(100, "demo-mint-1")
    assert response.status_code == 201
    body = response.json()
    assert body == {"operationId": body["operationId"], "type": "CONVERT_TO_TOKENIZED", "accountId": 101,
                    "quantity": 100, "traditionalUnits": 900, "tokenizedUnits": 100}
    lines = probe.rows("ledger_lines_for_operation", operation_id=body["operationId"])
    assert [(r["account_id"], r["representation"], r["units_delta"]) for r in lines] == [
        (101, "TRADITIONAL", -100), (101, "TOKENIZED", 100),
    ]
    STEP["mint"] = body


def test_l7_alpha_transfers_25_to_beta(users, probe):
    response = users["alpha"].transfer(102, 25, "demo-xfer-1")
    assert response.status_code == 201
    body = response.json()
    # Only the SENDER's new balance: the recipient's balance is private (a participant sees only
    # their own account), so it is never in the response, nor in the stored result that replays.
    assert body == {"operationId": body["operationId"], "type": "TRANSFER", "sourceAccountId": 101,
                    "destinationAccountId": 102, "quantity": 25, "sourceTokenizedUnits": 75}
    assert probe.tokenized(102) == 25  # the destination was credited
    STEP["xfer"] = {"body": body, "text": response.text}


def test_l6_beta_burns_10(users):
    response = users["beta"].burn(10, "demo-burn-1")
    assert response.status_code == 201
    assert (response.json()["traditionalUnits"], response.json()["tokenizedUnits"]) == (1010, 15)


def test_l7_beta_to_alpha_where_the_destination_has_the_lower_id(users, probe):
    # lock_positions returns 101 before 102; the app must still read the SOURCE (102) row.
    # (At these balances a rows[0] bug would still pass - alpha's 75 covers 5 - so the rows[0]
    #  trap is caught by test_60::test_the_source_row_is_found_by_account_id_not_by_position.)
    response = users["beta"].transfer(101, 5, "demo-xfer-2")
    assert response.status_code == 201
    assert response.json()["sourceTokenizedUnits"] == 10
    assert probe.tokenized(101) == 80


def test_positions_after_the_sequence(probe):
    assert probe.positions() == "101:900/80 102:1010/10 103:1000/0 104:0/0"


def test_l5_holdings_for_alpha(users):
    assert [(r["traditional_units"], r["tokenized_units"], r["total_units"])
            for r in users["alpha"].get("/api/holdings").json()] == [(900, 80, 980)]


def test_every_operation_except_opening_nets_to_zero(probe):
    nets = probe.rows("ledger_net_per_operation")
    assert nets and all(r["net"] == 0 for r in nets if r["operation_type"] != "OPENING")


# ---------- Lesson 10: history ----------

def history(api):
    return [(r["operation_type"], r["source_account_id"], r["destination_account_id"], r["quantity"],
             r["actor_username"]) for r in api.get("/api/operations").json()]


def test_l10_beta_history(users):
    assert history(users["beta"]) == [
        ("TRANSFER", 102, 101, 5, "beta"),
        ("CONVERT_TO_TRADITIONAL", 102, None, 10, "beta"),
        ("TRANSFER", 101, 102, 25, "alpha"),
        ("OPENING", None, 102, 1000, None),
    ]


def test_l10_alpha_never_sees_betas_private_burn(users):
    assert [row[0] for row in history(users["alpha"])] == ["TRANSFER", "TRANSFER", "CONVERT_TO_TOKENIZED", "OPENING"]


def test_l10_created_at_is_an_iso_timestamp_with_a_time_zone(users):
    created = users["alpha"].get("/api/operations").json()[0]["created_at"]
    assert datetime.fromisoformat(created).tzinfo is not None


def test_l10_auditor_sees_all_seven_newest_first(users):
    rows = users["auditor"].get("/api/operations").json()
    ids = [r["id"] for r in rows]
    assert len(rows) == 7 and ids == sorted(ids, reverse=True)


# ---------- Lesson 9: reconciliation ----------

def test_l9_reconciliation_after_the_sequence(users):
    report = users["auditor"].get("/api/reconciliation").json()
    assert report["passed"] is True
    assert report["conservation"] == [{"symbol": "DEMO-TREASURY", "total_custodied_units": 3000,
                                       "traditional_total": 2910, "tokenized_total": 90, "held_total": 3000,
                                       "ok": True}]
    assert [(r["account_id"], r["asset_id"], r["representation"], r["stored_units"], r["ledger_units"], r["ok"])
            for r in report["positionsVsLedger"]] == [
        (101, 1, "TOKENIZED", 80, 80, True), (101, 1, "TRADITIONAL", 900, 900, True),
        (102, 1, "TOKENIZED", 10, 10, True), (102, 1, "TRADITIONAL", 1010, 1010, True),
        (103, 1, "TOKENIZED", 0, 0, True), (103, 1, "TRADITIONAL", 1000, 1000, True),
        (104, 1, "TOKENIZED", 0, 0, True), (104, 1, "TRADITIONAL", 0, 0, True),
    ]


def test_l9_every_number_is_a_json_integer_and_ok_is_a_boolean(users):
    report = users["auditor"].get("/api/reconciliation").json()
    for row in report["conservation"]:
        numbers = ("total_custodied_units", "traditional_total", "tokenized_total", "held_total")
        assert all(type(row[f]) is int for f in numbers)
    for row in report["positionsVsLedger"]:
        assert type(row["stored_units"]) is int and type(row["ledger_units"]) is int and type(row["ok"]) is bool


# ---------- Lesson 8: idempotency ----------

def test_l8_replay_returns_the_stored_result_and_applies_nothing(users, probe):
    before = probe.snapshot()
    response = users["alpha"].transfer(102, 25, "demo-xfer-1")
    assert response.status_code == 200
    assert response.headers["idempotency-replayed"] == "true"
    assert response.json() == STEP["xfer"]["body"]  # compare PARSED json ...
    assert response.text != STEP["xfer"]["text"]  # ... because JSONB reorders the keys
    assert probe.snapshot() == before
    assert probe.rows_for_key(11, "demo-xfer-1") == 1


def test_l8_replay_of_a_conversion(users, probe):
    before = probe.snapshot()
    response = users["alpha"].mint(100, "demo-mint-1")
    assert response.status_code == 200 and response.json() == STEP["mint"]
    assert probe.snapshot() == before


def test_l8_same_key_with_a_different_body_is_409(users, probe):
    before = probe.snapshot()
    response = users["alpha"].transfer(102, 26, "demo-xfer-1")
    assert response.status_code == 409
    assert response.json() == {"error": "idempotency key reused for a different request"}
    assert users["alpha"].mint(25, "demo-xfer-1").status_code == 409  # same key, other endpoint
    assert probe.snapshot() == before


def test_l8_keys_are_per_actor(users):
    response = users["beta"].transfer(103, 5, "demo-xfer-1")
    assert response.status_code == 201
    assert response.json()["sourceTokenizedUnits"] == 5


# ---------- rejections: each one leaves nothing behind ----------

T, C = "/api/transfers", "/api/conversions"
QTY = "quantity must be a whole number 1..1000000"
TO = "toAccountId must be a whole number 1..2147483647"
KEY = "idempotency key must be 1..100 characters (no NUL)"
DIRECTION = "direction must be TO_TOKENIZED or TO_TRADITIONAL"


def xfer(to=102, quantity=5, key="rej-x"):
    return {"toAccountId": to, "quantity": quantity, "idempotencyKey": key}


def conv(direction="TO_TOKENIZED", quantity=5, key="rej-x"):
    return {"direction": direction, "quantity": quantity, "idempotencyKey": key}


REJECTIONS = [
    # id, user, path, body, status, error
    ("unapproved-destination", "alpha", T, xfer(104, 5, "rej-1"), 422, "wallet is not approved"),
    ("more-than-held", "alpha", T, xfer(102, 1000, "rej-2"), 422, "insufficient tokenized units"),
    ("mint-more-than-held", "alpha", C, conv("TO_TOKENIZED", 5000, "rej-3"), 422, "insufficient traditional units"),
    ("burn-more-than-held", "alpha", C, conv("TO_TRADITIONAL", 1000, "rej-3b"), 422, "insufficient tokenized units"),
    ("auditor-transfer", "auditor", T, xfer(102, 5, "rej-4"), 403, "participants only"),
    ("auditor-convert", "auditor", C, conv("TO_TOKENIZED", 5, "rej-5"), 403, "participants only"),
    ("unknown-account", "alpha", T, xfer(99, 5, "rej-6"), 422, "unknown account"),
    ("unknown-account-max-int", "alpha", T, xfer(2147483647, 5, "rej-6b"), 422, "unknown account"),
    ("self-transfer", "alpha", T, xfer(101, 5, "rej-7"), 400, "cannot transfer to yourself"),
    ("quantity-0", "alpha", T, xfer(102, 0, "rej-9"), 400, QTY),
    ("quantity-1.5", "alpha", C, conv("TO_TOKENIZED", 1.5, "rej-10"), 400, QTY),
    ("quantity-5.0", "alpha", T, xfer(102, 5.0, "rej-10b"), 400, QTY),
    ("quantity-string", "alpha", T, xfer(102, "5", "rej-10c"), 400, QTY),
    ("quantity-true", "alpha", T, xfer(102, True, "rej-10d"), 400, QTY),
    ("quantity-1000001", "alpha", C, conv("TO_TOKENIZED", 1000001, "rej-11"), 400, QTY),
    ("to-3000000000", "alpha", T, xfer(3000000000, 5, "rej-12"), 400, TO),
    ("to-0", "alpha", T, xfer(0, 5, "rej-12b"), 400, TO),
    ("to-string", "alpha", T, xfer("102", 5, "rej-12c"), 400, TO),
    ("empty-key", "alpha", T, xfer(102, 5, ""), 400, KEY),
    ("101-char-key", "alpha", T, xfer(102, 5, "k" * 101), 400, KEY),
    ("nul-in-key", "alpha", T, xfer(102, 5, "rej-\x00"), 400, KEY),
    ("number-key", "alpha", T, xfer(102, 5, 123), 400, KEY),
    ("bad-direction", "alpha", C, conv("SIDEWAYS", 5, "rej-13"), 400, DIRECTION),
    ("missing-fields", "alpha", T, {"toAccountId": 102}, 400, QTY),
    ("not-an-object", "alpha", T, [1, 2], 400, "request body must be a JSON object with the documented fields"),
]


@pytest.mark.parametrize("case", REJECTIONS, ids=[c[0] for c in REJECTIONS])
def test_rejection_leaves_nothing(users, probe, case):
    _, user, path, payload, status, error = case
    before = probe.snapshot()
    response = users[user].post(path, payload)
    assert response.status_code == status, response.text
    assert response.json()["error"] == error
    assert probe.snapshot() == before


def test_unknown_account_names_the_foreign_key(users):
    assert users["alpha"].transfer(99, 5, "rej-6c").json()["constraint"] == "operations_destination_account_id_fkey"


def test_malformed_json_is_400(users, probe):
    before = probe.snapshot()
    response = users["alpha"].client.post(
        "/api/transfers", content=b'{"toAccountId": 102,', headers={"content-type": "application/json"}
    )
    assert response.status_code == 400 and response.json() == {"error": "request body must be valid JSON"}
    assert probe.snapshot() == before


def test_source_wallet_not_approved(users, probe):
    before = probe.snapshot()
    try:
        probe.execute("set_approval", is_approved=False, account_id=103)  # reversible demo step
        response = users["gamma"].transfer(101, 1, "rej-14")
    finally:
        probe.execute("set_approval", is_approved=True, account_id=103)
    assert response.status_code == 422 and response.json() == {"error": "wallet is not approved"}
    assert probe.snapshot() == before


def test_db_backstop_self_transfer_when_the_app_check_is_skipped(database_url, probe):
    before = probe.snapshot()
    with psycopg.connect(database_url) as conn:  # straight to the domain: no route, no 400 check
        with pytest.raises(pg.CheckViolation) as caught:
            transfers.transfer(conn, actor_user_id=11, source_account_id=101, destination_account_id=101,
                               asset_id=1, quantity=5, idempotency_key="rej-8")
    assert classify_db_error(caught.value) == (
        422, {"error": "rule violated", "pgCode": "23514", "constraint": "operations_no_self_transfer"})
    assert probe.snapshot() == before


def test_db_backstop_4000_character_key_when_the_app_check_is_skipped(database_url, probe):
    before = probe.snapshot()
    with psycopg.connect(database_url) as conn:
        with pytest.raises(pg.CheckViolation) as caught:  # 23514, never 54000 "index row size"
            transfers.transfer(conn, actor_user_id=11, source_account_id=101, destination_account_id=102,
                               asset_id=1, quantity=5, idempotency_key="k" * 4000)
    assert caught.value.diag.constraint_name == "operations_idempotency_key_check"
    assert classify_db_error(caught.value)[0] == 422
    assert probe.snapshot() == before


def test_rejections_left_no_operation_rows(probe):
    assert probe.value("keys_like", pattern="rej-%") == 0


def test_a_rejected_key_can_be_reused(users):
    assert users["alpha"].transfer(102, 5, "rej-1").status_code == 201  # rej-1 was the Delta 422


def test_final_reconciliation_passes(users):
    report = users["auditor"].get("/api/reconciliation").json()
    assert report["passed"] is True, report

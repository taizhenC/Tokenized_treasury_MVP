"""Authorization across every endpoint (needs Lessons 6-14): the auditor never writes, the operator
runs only the end-of-day steps, a participant acts and reads only for their own account, and every
endpoint needs a login. No balance changes here (every write below is refused, and the one order
planted to test the views is removed again), so test_50 can still check the exact lesson numbers."""

import pytest

from tests.helpers import new_key

ENDPOINTS = [
    ("GET", "/api/me"), ("GET", "/api/holdings"), ("GET", "/api/accounts"), ("GET", "/api/operations"),
    ("GET", "/api/reconciliation"), ("POST", "/api/conversions"), ("POST", "/api/transfers"),
    # Lessons 12-14: the cash leg
    ("GET", "/api/settlement/day"), ("GET", "/api/settlement/accounts"), ("GET", "/api/settlement/closed-days"),
    ("GET", "/api/delivery-orders"), ("POST", "/api/delivery-orders"), ("POST", "/api/settlement-progress-payments"),
    ("POST", "/api/settlement-day/valued-cutoff"), ("POST", "/api/settlement-day/final-settlement"),
]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_every_endpoint_needs_a_login(anonymous, method, path):
    body = {"direction": "TO_TOKENIZED", "toAccountId": 102, "quantity": 1, "amountCents": 100,
            "idempotencyKey": "anon"}
    response = anonymous.client.request(method, path, json=body if method == "POST" else None)
    assert response.status_code == 401
    assert response.json() == {"error": "log in first"}


def test_an_oversized_body_is_413_with_or_without_a_login(users, anonymous, probe):
    """FastAPI parses the body before the login check, so the size limit must come first."""
    before = probe.snapshot()
    huge = {"toAccountId": 102, "quantity": 1, "idempotencyKey": "k" * 50_000}
    for client in (anonymous, users["alpha"]):
        for path in ("/api/transfers", "/api/delivery-orders"):
            response = client.post(path, {**huge, "amountCents": 100} if path == "/api/delivery-orders" else huge)
            assert response.status_code == 413
            assert response.json() == {"error": "request body too large"}
    assert probe.snapshot() == before


def test_auditor_is_read_only(users, probe):
    before = probe.snapshot()
    auditor = users["auditor"]
    for response in (auditor.transfer(102, 5, "aud-1"), auditor.mint(5, "aud-2"), auditor.burn(5, "aud-3")):
        assert response.status_code == 403
        assert response.json() == {"error": "participants only"}
    assert probe.snapshot() == before


def test_participants_cannot_see_the_reconciliation_report(users):
    response = users["alpha"].get("/api/reconciliation")
    assert response.status_code == 403
    assert response.json() == {"error": "auditors only"}


@pytest.mark.parametrize("field", ["fromAccountId", "sourceAccountId", "accountId", "actorUserId"])
def test_a_participant_cannot_name_another_account_in_the_body(users, probe, field):
    before = probe.snapshot()
    body = {"toAccountId": 103, "quantity": 1, "idempotencyKey": f"spoof-{field}", field: 102}
    response = users["alpha"].post("/api/transfers", body)
    assert response.status_code == 400
    assert response.json() == {"error": f"unexpected field: {field}"}
    conversion = {"direction": "TO_TOKENIZED", "quantity": 1, "idempotencyKey": f"spoof-c-{field}", field: 102}
    assert users["alpha"].post("/api/conversions", conversion).status_code == 400
    assert probe.snapshot() == before


def test_query_parameters_cannot_widen_a_participants_view(users):
    rows = users["alpha"].get("/api/holdings", params={"account_id": 102, "accountId": 102}).json()
    assert [r["account_id"] for r in rows] == [101]
    history = users["alpha"].get("/api/operations", params={"account_id": 102}).json()
    assert all(101 in (r["source_account_id"], r["destination_account_id"]) for r in history)


# ---------- Lessons 12-14: the settlement desk and the cash leg ----------

WRITES = {
    "deliver": lambda api: api.deliver(103, 1, 100, new_key("authz-deliver")),
    "pay": lambda api: api.pay(100, new_key("authz-pay")),
    "mint": lambda api: api.mint(1, new_key("authz-mint")),
    "burn": lambda api: api.burn(1, new_key("authz-burn")),
    "transfer": lambda api: api.transfer(103, 1, new_key("authz-transfer")),
    "valued_cutoff": lambda api: api.valued_cutoff(new_key("authz-cutoff")),
    "final_settlement": lambda api: api.final_settlement(new_key("authz-final")),
}
REFUSED_WRITES = [
    # Only participants move units or money; the auditor and the operator hold no account.
    *[(user, write, "participants only") for user in ("auditor", "operator") for write in ("deliver", "pay")],
    *[("operator", write, "participants only") for write in ("mint", "burn", "transfer")],
    # Only the settlement desk ends the day.
    *[(user, write, "operators only")
      for user in ("alpha", "beta", "gamma", "auditor") for write in ("valued_cutoff", "final_settlement")],
]


@pytest.mark.parametrize("user,write,error", REFUSED_WRITES, ids=[f"{u}-{w}" for u, w, _ in REFUSED_WRITES])
def test_who_may_write_what(users, probe, user, write, error):
    before = probe.snapshot()
    response = WRITES[write](users[user])
    assert response.status_code == 403
    assert response.json() == {"error": error}
    assert probe.snapshot() == before


@pytest.mark.parametrize("user,path,error", [
    ("alpha", "/api/settlement/closed-days", "auditors and operators only"),  # the figures list every account
    ("beta", "/api/settlement/closed-days", "auditors and operators only"),
    ("gamma", "/api/settlement/closed-days", "auditors and operators only"),
    ("operator", "/api/reconciliation", "auditors only"),  # separation of duties: the desk does not audit itself
])
def test_refused_reads(users, user, path, error):
    response = users[user].get(path)
    assert response.status_code == 403
    assert response.json() == {"error": error}


@pytest.mark.parametrize("user", ["alpha", "beta", "gamma", "auditor", "operator"])
def test_settlement_reads_are_open_to_every_signed_in_user(users, user):
    for path in ("/api/settlement/day", "/api/settlement/accounts", "/api/delivery-orders"):
        response = users[user].get(path)
        assert response.status_code == 200, (path, response.text)


def test_the_operator_reads_like_the_auditor(users):
    for path in ("/api/holdings", "/api/operations", "/api/settlement/accounts", "/api/settlement/closed-days"):
        operator, auditor = users["operator"].get(path), users["auditor"].get(path)
        assert operator.status_code == auditor.status_code == 200, path
        assert operator.json() == auditor.json(), path
    assert [r["account_id"] for r in users["operator"].get("/api/holdings").json()] == [101, 102, 103, 104]


SPOOF_FIELDS = ["fromAccountId", "delivererAccountId", "sourceAccountId", "accountId", "actorUserId"]


@pytest.mark.parametrize("field", SPOOF_FIELDS)
def test_a_participant_cannot_name_another_account_in_a_settlement_body(users, probe, field):
    before = probe.snapshot()
    order = {"toAccountId": 103, "quantity": 1, "amountCents": 100, "idempotencyKey": f"spoof-d-{field}", field: 102}
    response = users["alpha"].post("/api/delivery-orders", order)
    assert response.status_code == 400
    assert response.json() == {"error": f"unexpected field: {field}"}
    payment = {"amountCents": 100, "idempotencyKey": f"spoof-p-{field}", field: 102}
    response = users["alpha"].post("/api/settlement-progress-payments", payment)
    assert response.status_code == 400
    assert response.json() == {"error": f"unexpected field: {field}"}
    assert probe.snapshot() == before


@pytest.mark.parametrize("field,value", [("businessDate", "2026-10-20"), ("phase", "OPEN"), ("accountId", 101)])
def test_the_operator_cannot_choose_the_day_or_an_account(users, probe, field, value):
    """Which day is cut off or closed always comes from the database row, never from the request."""
    before = probe.snapshot()
    for path in ("/api/settlement-day/valued-cutoff", "/api/settlement-day/final-settlement"):
        response = users["operator"].post(path, {"idempotencyKey": new_key("spoof-day"), field: value})
        assert response.status_code == 400
        assert response.json() == {"error": f"unexpected field: {field}"}
    assert probe.snapshot() == before


@pytest.fixture
def order_beta_to_gamma(probe):
    """A deliver order between two OTHER participants, planted directly (no request runs it, so
    nothing settles and no balance changes) and always removed again."""
    instruction_id = probe.value("plant_instruction_beta_to_gamma", idempotency_key=new_key("planted"))
    try:
        yield instruction_id
    finally:
        assert probe.execute("remove_planted_instruction", instruction_id=instruction_id) == 1


def test_query_parameters_cannot_widen_a_participants_settlement_views(users, probe, order_beta_to_gamma):
    before = probe.snapshot()
    rows = users["alpha"].get("/api/settlement/accounts", params={"account_id": 102, "accountId": 102}).json()
    assert [r["account_id"] for r in rows] == [101]
    every = users["auditor"].get("/api/settlement/accounts", params={"account_id": 102}).json()
    assert [r["account_id"] for r in every] == [101, 102, 103, 104]

    def order_ids(user, **params):
        return [r["id"] for r in users[user].get("/api/delivery-orders", params=params).json()]

    for user in ("beta", "gamma", "auditor", "operator"):  # its two sides, and the overseers
        assert order_beta_to_gamma in order_ids(user)
    for params in ({}, {"account_id": 102}, {"accountId": 103}, {"deliverer_account_id": 102}, {"all": "true"}):
        rows = users["alpha"].get("/api/delivery-orders", params=params).json()
        assert order_beta_to_gamma not in [r["id"] for r in rows]
        assert all(101 in (r["deliverer_account_id"], r["receiver_account_id"]) for r in rows)
    assert probe.snapshot() == before


def test_each_side_of_an_order_sees_only_its_own_recycle_reason(users, order_beta_to_gamma):
    """A lab choice: beta (the deliverer) learns that gamma is the hold-up, not that gamma is near
    its net debit cap. Gamma, the auditor and the operator see the exact reason."""
    def reason(user):
        rows = users[user].get("/api/delivery-orders").json()
        return next(r["recycle_reason"] for r in rows if r["id"] == order_beta_to_gamma)

    assert reason("beta") == "WAITING_ON_COUNTERPARTY"
    assert reason("gamma") == reason("auditor") == reason("operator") == "RECEIVER_NET_DEBIT_CAP"

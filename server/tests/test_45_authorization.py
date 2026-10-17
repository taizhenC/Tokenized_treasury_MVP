"""Authorization across every endpoint (needs Lessons 6-10): the auditor never writes, a
participant acts and reads only for their own account, and every endpoint needs a login.
No balances change here, so test_50 can still check the exact lesson numbers."""

import pytest


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/api/me"), ("GET", "/api/holdings"), ("GET", "/api/accounts"), ("GET", "/api/operations"),
     ("GET", "/api/reconciliation"), ("POST", "/api/conversions"), ("POST", "/api/transfers")],
)
def test_every_endpoint_needs_a_login(anonymous, method, path):
    body = {"direction": "TO_TOKENIZED", "toAccountId": 102, "quantity": 1, "idempotencyKey": "anon"}
    response = anonymous.client.request(method, path, json=body if method == "POST" else None)
    assert response.status_code == 401
    assert response.json() == {"error": "log in first"}


def test_an_oversized_body_is_413_with_or_without_a_login(users, anonymous, probe):
    """FastAPI parses the body before the login check, so the size limit must come first."""
    before = probe.snapshot()
    huge = {"toAccountId": 102, "quantity": 1, "idempotencyKey": "k" * 50_000}
    for client in (anonymous, users["alpha"]):
        response = client.post("/api/transfers", huge)
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



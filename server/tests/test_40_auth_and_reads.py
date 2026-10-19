"""Lesson 5: login, logout, the session cookie, and the read queries (authorization: test_45)."""

import asyncio
import json
from base64 import b64encode
from contextlib import contextmanager

import httpx
import itsdangerous
import psycopg
import pytest

from app import queries
from app.main import create_app
from app.settings import Settings
from tests.helpers import PASSWORD, SESSION_COOKIE, Api, run

HOLDINGS = queries.load("holdings", queries.HOLDINGS_BLOCKS)
AUTH = queries.load("auth", queries.AUTH_BLOCKS)


# ---------- login / logout / session ----------

def test_wrong_password_and_unknown_user_get_the_same_401(anonymous):
    wrong = anonymous.login("alpha", "wrong")
    unknown = anonymous.login("nobody", PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"error": "invalid username or password"}
    assert anonymous.cookie is None


def test_each_user_logs_in_and_me_returns_the_session_user(users):
    me = {name: api.get("/api/me").json() for name, api in users.items()}
    assert me == {
        "alpha": {"id": 11, "username": "alpha", "role": "PARTICIPANT", "account_id": 101},
        "beta": {"id": 12, "username": "beta", "role": "PARTICIPANT", "account_id": 102},
        "gamma": {"id": 13, "username": "gamma", "role": "PARTICIPANT", "account_id": 103},
        "auditor": {"id": 14, "username": "auditor", "role": "AUDITOR", "account_id": None},
        "operator": {"id": 15, "username": "operator", "role": "OPERATOR", "account_id": None},
    }


def test_login_cookie_is_httponly_lax_and_expires(anonymous):
    response = anonymous.login("alpha")
    assert response.status_code == 200
    assert "password_hash" not in response.json()
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for flag in ("httponly", "samesite=lax", "path=/", "max-age=28800"):
        assert flag in cookie
    assert "secure" not in cookie  # the tests run with SESSION_COOKIE_SECURE=false (plain http)


def test_cookie_is_secure_when_configured(live_server):
    secure_app = create_app(Settings(session_secret="s" * 40, session_cookie_secure=True))

    async def login():  # in-process ASGI call, no lifespan: it reuses the pool the live server opened
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=secure_app), base_url="https://lab") as client:
            return await client.post("/api/login", json={"username": "alpha", "password": PASSWORD})

    response = asyncio.run(login())
    assert response.status_code == 200
    assert "; secure" in response.headers["set-cookie"].lower()


def test_logout_clears_the_cookie(live_server):
    api = Api(live_server)
    assert api.login("beta").status_code == 200
    assert api.get("/api/me").status_code == 200
    out = api.post("/api/logout")
    assert out.status_code == 204
    assert "expires=thu, 01 jan 1970" in out.headers["set-cookie"].lower()
    assert api.get("/api/me").status_code == 401


def test_known_limitation_a_copied_cookie_still_works_after_logout(live_server):
    """Signed-cookie sessions are stateless: logout deletes the cookie in THIS browser, but a copy
    taken earlier stays valid until max_age (8 h). See the README's known limitations."""
    api = Api(live_server)
    api.login("gamma")
    copied = api.cookie
    api.post("/api/logout")
    assert Api(live_server, copied).get("/api/me").status_code == 200


@pytest.mark.parametrize("path", ["/api/me", "/api/holdings", "/api/accounts"])
def test_lesson5_endpoints_need_a_login(anonymous, path):
    response = anonymous.get(path)
    assert response.status_code == 401
    assert response.json() == {"error": "log in first"}


def test_a_tampered_cookie_is_ignored(live_server, users):
    value = users["alpha"].cookie
    tampered = value[:-2] + ("AA" if not value.endswith("AA") else "BB")
    assert Api(live_server, tampered).get("/api/me").status_code == 401


def test_a_validly_signed_cookie_for_a_missing_user_is_rejected(live_server):
    import os

    signer = itsdangerous.TimestampSigner(os.environ["SESSION_SECRET"])
    forged = signer.sign(b64encode(json.dumps({"user_id": 999}).encode())).decode()
    assert Api(live_server, forged).get("/api/me").status_code == 401


def test_login_gives_the_connection_back_before_the_slow_password_check(live_server, monkeypatch):
    """bcrypt takes about 45 ms; holding a pooled connection (idle in transaction) during it would let
    a stream of failed logins tie up the pool that every logged-in request needs."""
    from app.db import pool
    from app.domain import auth

    class CountingPool:  # the real pool, counting connections that are lent out and not yet back
        in_use = 0
        lent = 0

        @contextmanager
        def connection(self, timeout=None):
            self.in_use += 1
            self.lent += 1
            try:
                with pool.connection(timeout) as conn:
                    yield conn
            finally:
                self.in_use -= 1  # after the pool took the connection back

    counting = CountingPool()
    in_use_during_check = []
    real_checkpw = auth.bcrypt.checkpw

    def watching_checkpw(secret, stored):
        in_use_during_check.append(counting.in_use)
        return real_checkpw(secret, stored)

    monkeypatch.setattr(auth.bcrypt, "checkpw", watching_checkpw)
    user = auth.authenticate(counting, "alpha", PASSWORD)
    assert user == {"id": 11, "username": "alpha", "role": "PARTICIPANT", "account_id": 101}
    assert auth.authenticate(counting, "alpha", "wrong") is None
    assert auth.authenticate(counting, "nobody", PASSWORD) is None
    assert in_use_during_check == [0, 0, 0]
    assert counting.lent == 3  # one short lookup per attempt


def test_request_bodies_over_16_kib_are_refused_before_anything_reads_them(anonymous):
    big = {"username": "alpha", "password": "x" * 100_000}
    response = anonymous.post("/api/login", big)  # Content-Length says it is too big
    assert response.status_code == 413
    assert response.json() == {"error": "request body too large"}

    def chunks():  # no Content-Length: sent chunked, counted while it is read
        for _ in range(8):
            yield b" " * 4096

    chunked = anonymous.client.post("/api/login", content=chunks(), headers={"content-type": "application/json"})
    assert chunked.status_code == 413
    assert chunked.json() == {"error": "request body too large"}
    assert anonymous.login("alpha").status_code == 200  # normal bodies are untouched


def test_login_input_checks(anonymous):
    too_long = "x" * 73  # bcrypt reads at most 72 bytes; the bcrypt package raises beyond that
    assert anonymous.login("alpha", too_long).status_code == 401
    assert anonymous.post("/api/login", {"username": "al\x00pha", "password": PASSWORD}).status_code == 400
    assert anonymous.post("/api/login", {"username": "alpha"}).status_code == 400
    extra_field = {"username": "alpha", "password": PASSWORD, "role": "AUDITOR"}
    assert anonymous.post("/api/login", extra_field).status_code == 400


# ---------- reads ----------

def test_holdings_participant_sees_one_row_auditor_sees_four(users):
    alpha = users["alpha"].get("/api/holdings").json()
    assert alpha == [{
        "account_id": 101, "participant_name": "Participant Alpha (demo)", "wallet_ref": "SIM-WALLET-ALPHA",
        "is_approved": True, "symbol": "DEMO-TREASURY", "traditional_units": 1000, "tokenized_units": 0,
        "total_units": 1000,
    }]
    every = users["auditor"].get("/api/holdings").json()
    assert [(r["account_id"], r["is_approved"], r["total_units"]) for r in every] == [
        (101, True, 1000), (102, True, 1000), (103, True, 1000), (104, False, 0),
    ]
    assert all(type(r[f]) is int for r in every for f in ("traditional_units", "tokenized_units", "total_units"))


def test_account_directory(users):
    rows = users["beta"].get("/api/accounts").json()
    assert [(r["id"], r["is_approved"]) for r in rows] == [(101, True), (102, True), (103, True), (104, False)]
    assert "traditional_units" not in rows[0]


def test_lesson5_statements_directly(database_url):
    with psycopg.connect(database_url) as conn:
        asset = run(conn, HOLDINGS["find_asset_by_symbol"], {"symbol": "DEMO-TREASURY"}).fetchone()
        assert asset == {"id": 1, "symbol": "DEMO-TREASURY", "total_custodied_units": 3000, "unit_precision": 0}
        assert run(conn, HOLDINGS["holdings_for_account"], {"account_id": 11}).fetchall() == []  # a USER id
        assert run(conn, AUTH["find_user_by_username"], {"username": "nobody"}).fetchall() == []
        cur = run(conn, AUTH["find_user_by_id"], {"user_id": 14})
        assert [c.name for c in cur.description] == ["id", "username", "role", "account_id"]
        assert cur.fetchone() == {"id": 14, "username": "auditor", "role": "AUDITOR", "account_id": None}

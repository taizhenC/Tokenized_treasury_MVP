"""Small tools shared by the tests: SQL probes, an HTTP client per user, lock holders, waiting."""

import ssl
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

from app.sql import load_sql_blocks

TESTS_DIR = Path(__file__).resolve().parent
PROBES = load_sql_blocks(TESTS_DIR / "sql" / "probes.sql")
CONSTRAINT_ATTACKS = load_sql_blocks(TESTS_DIR / "sql" / "constraints.sql")
FIXTURES = load_sql_blocks(TESTS_DIR / "sql" / "fixtures.sql")

PASSWORD = "demo-password-1"
SESSION_COOKIE = "ttlab_session"
SEEDED_POSITIONS = "101:1000/0 102:1000/0 103:1000/0 104:0/0"
# Loading the CA bundle takes ~180 ms per client on Windows; build it once and share it.
_SSL_CONTEXT = ssl.create_default_context()


def run(conn: psycopg.Connection, sql: str, params: dict[str, Any] | None = None) -> psycopg.Cursor:
    """Execute a block. A block without placeholders is sent as-is (no %-parsing)."""
    return conn.cursor(row_factory=dict_row).execute(sql, params if "%(" in sql else None)


class Probe:
    """The tests' own SQL (tests/sql/probes.sql) on one autocommit connection."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def rows(self, block: str, /, **params: Any) -> list[dict[str, Any]]:
        return run(self.conn, PROBES[block], params).fetchall()

    def one(self, block: str, /, **params: Any) -> dict[str, Any]:
        return run(self.conn, PROBES[block], params).fetchone()

    def execute(self, block: str, /, **params: Any) -> int:
        """For statements that return no rows (UPDATE, LOCK, SET ...): the rowcount."""
        return run(self.conn, PROBES[block], params).rowcount

    def value(self, block: str, /, **params: Any) -> Any:
        return next(iter(self.one(block, **params).values()))

    def tokenized(self, account_id: int) -> int:
        return self.value("tokenized_units", account_id=account_id)

    def rows_for_key(self, actor_user_id: int, key: str) -> int:
        return self.value("rows_for_key", actor_user_id=actor_user_id, idempotency_key=key)

    def positions(self) -> str:
        return " ".join(
            f"{r['account_id']}:{r['traditional_units']}/{r['tokenized_units']}" for r in self.rows("positions")
        )

    def snapshot(self) -> dict[str, Any]:
        """Everything a rejected request must leave unchanged."""
        counts = self.one("counts")
        return {"positions": self.positions(), **counts}

    def run_block(self, sql: str, /, **params: Any) -> list[dict[str, Any]]:
        cur = run(self.conn, sql, params)
        return cur.fetchall() if cur.description else []


class Api:
    """One logged-in browser: an httpx client with its own cookie jar."""

    def __init__(self, base_url: str, cookie: str | None = None) -> None:
        self.base_url = base_url
        self.client = httpx.Client(base_url=base_url, timeout=60, verify=_SSL_CONTEXT)
        if cookie:
            self.client.cookies.set(SESSION_COOKIE, cookie)

    def login(self, username: str, password: str = PASSWORD) -> httpx.Response:
        return self.client.post("/api/login", json={"username": username, "password": password})

    @property
    def cookie(self) -> str | None:
        return self.client.cookies.get(SESSION_COOKIE)

    def fork(self) -> "Api":
        """A second client with the same session (another tab), for use on another thread."""
        return Api(self.base_url, self.cookie)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.get(path, **kwargs)

    def post(self, path: str, json: Any = None, **kwargs: Any) -> httpx.Response:
        return self.client.post(path, json=json, **kwargs)

    def transfer(self, to: int, quantity: int, key: str) -> httpx.Response:
        return self.post("/api/transfers", {"toAccountId": to, "quantity": quantity, "idempotencyKey": key})

    def convert(self, direction: str, quantity: int, key: str) -> httpx.Response:
        return self.post("/api/conversions", {"direction": direction, "quantity": quantity, "idempotencyKey": key})

    def mint(self, quantity: int, key: str) -> httpx.Response:
        return self.convert("TO_TOKENIZED", quantity, key)

    def burn(self, quantity: int, key: str) -> httpx.Response:
        return self.convert("TO_TRADITIONAL", quantity, key)

    def close(self) -> None:
        self.client.close()


class Blocker:
    """A separate connection that holds a lock until release(): it makes requests wait at a
    known step, so the tests can line up real concurrent requests without sleeping."""

    def __init__(self, database_url: str) -> None:
        self.conn = psycopg.connect(database_url)  # not autocommit: locks last until rollback

    def hold(self, block: str, /, **params: Any) -> None:
        run(self.conn, PROBES[block], params)

    def release(self) -> None:
        self.conn.rollback()

    def __enter__(self) -> "Blocker":
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.rollback()
        self.conn.close()


def wait_for_lock_waiters(probe: Probe, needle: str, count: int, timeout: float = 20) -> list[dict[str, Any]]:
    """Poll pg_stat_activity until `count` backends are waiting on a lock in a statement containing `needle`."""
    deadline = time.monotonic() + timeout
    while True:
        rows = probe.rows("lock_waiters", needle=needle)
        if len(rows) >= count:
            return rows
        if time.monotonic() > deadline:
            raise AssertionError(f"expected {count} backend(s) waiting on {needle!r}, saw {len(rows)}")
        time.sleep(0.02)


_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="client")


def in_background(call: Callable[[], Any]) -> Future:
    """Start an HTTP call on another thread (use an Api.fork() per thread)."""
    return _executor.submit(call)


def run_together(*calls: Callable[[], Any]) -> list[Any]:
    """Start every call at the same instant, each on its own thread; results in call order."""
    barrier = threading.Barrier(len(calls))

    def at_the_barrier(call: Callable[[], Any]) -> Any:
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(at_the_barrier, call) for call in calls]
        return [future.result() for future in futures]


PARTICIPANT_OF = {101: "alpha", 102: "beta", 103: "gamma"}


def new_key(prefix: str) -> str:
    """A fresh idempotency key."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def set_tokenized(users: dict[str, Api], probe: Probe, account_id: int, target: int) -> None:
    """Bring an account's TOKENIZED balance to `target` through the API (burn the excess, or
    receive the shortfall from the participant who holds the most tokenized units)."""
    current = probe.tokenized(account_id)
    if current > target:
        response = users[PARTICIPANT_OF[account_id]].burn(current - target, new_key("trim"))
        assert response.status_code == 201, response.text
    elif current < target:
        need = target - current
        donor_id = max((a for a in PARTICIPANT_OF if a != account_id), key=probe.tokenized)
        donor = users[PARTICIPANT_OF[donor_id]]
        if probe.tokenized(donor_id) < need:
            minted = donor.mint(need - probe.tokenized(donor_id), new_key("fund"))
            assert minted.status_code == 201, minted.text
        response = donor.transfer(account_id, need, new_key("fill"))
        assert response.status_code == 201, response.text
    assert probe.tokenized(account_id) == target

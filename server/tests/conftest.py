"""Test setup: a REAL PostgreSQL database and a REAL uvicorn server - no mocks for DB behaviour.

Before the suite:  uv run python -m tests.reset_db   (empty database -> migrations -> seeds)
Then:              uv run pytest

The modules run in file-name order. test_10..test_40 leave balances untouched, so
test_50_demo_flow can check the exact numbers of the lesson acceptance sequence; the later
modules only assert relative changes.
"""

import os
import socket
import threading
import time

import pytest

from tests.safety import UnsafeDatabase, resolve_test_database_url

try:
    TEST_DATABASE_URL = resolve_test_database_url()
except UnsafeDatabase as err:
    pytest.exit(str(err), returncode=4)

# Before anything imports app.db: the app's pool must point at the TEST database.
# (load_dotenv never overrides a variable that is already set.)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SESSION_SECRET", "test-only-session-secret-0123456789abcdef")
os.environ["SESSION_COOKIE_SECURE"] = "false"

import psycopg  # noqa: E402
import uvicorn  # noqa: E402

from tests.helpers import Api, Probe  # noqa: E402


@pytest.fixture(scope="session")
def database_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def probe(database_url):
    """The tests' own SQL on one autocommit connection (outside every app transaction)."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        yield Probe(conn)


@pytest.fixture(scope="session")
def live_server():
    """The real app (app.main:app) served by uvicorn on a free port, in a background thread."""
    from app.main import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.should_exit = True
    thread.join(timeout=30)


@pytest.fixture(scope="session")
def users(live_server):
    """One logged-in client per seeded user."""
    clients = {}
    for name in ("alpha", "beta", "gamma", "auditor"):
        api = Api(live_server)
        response = api.login(name)
        assert response.status_code == 200, response.text
        clients[name] = api
    yield clients
    for api in clients.values():
        api.close()


@pytest.fixture
def anonymous(live_server):
    api = Api(live_server)
    yield api
    api.close()

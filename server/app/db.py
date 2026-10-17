import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg import Connection
from psycopg_pool import ConnectionPool

# Local development reads server/.env. On the host, real environment variables
# are already set, and load_dotenv never overrides them.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Copy server/.env.example to server/.env.")


def _check(conn: Connection) -> None:
    """Test a connection before lending it out; if it is dead, test every idle one now.

    One dead connection almost always means the database restarted, so every idle
    connection is dead. Without the pool.check() below, one request meets them one at a
    time while psycopg_pool sleeps 1, 2, 4, 8 and 16 s between attempts: measured 30 s
    and a 503 for the first request after a restart. With it: about 0.01 s.
    """
    try:
        ConnectionPool.check_connection(conn)
    except Exception:
        pool.check()  # replaces every broken idle connection with a fresh one
        raise  # the pool discards this connection and tries the next


# A pool keeps a few database connections open and lends one out per request,
# so each request does not pay the cost of opening a new connection.
# check makes the pool test a connection before lending it out, so a connection
# broken by a database restart gets replaced instead of used.
# connect_timeout matters: without it, an attempt to reach an unreachable database
# can hang forever, tying up the pool's background workers so it never recovers.
# lock_timeout=5s: a request that waits more than 5 s for a row lock (for example one
# held open by `db.try_block --hold` or a forgotten psql BEGIN) fails with 55P03 and
# answers 503, instead of holding a pooled connection and stalling every other request.
pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=10,
    open=False,
    check=_check,
    kwargs={"connect_timeout": 5, "options": "-c lock_timeout=5s"},
)

"""Login checks (Lesson 5). Passwords are verified with bcrypt; the session stores only the user id."""

from typing import Any

import bcrypt
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import queries

SQL = queries.load("auth", queries.AUTH_BLOCKS)

SESSION_USER_FIELDS = ("id", "username", "role", "account_id")  # never password_hash
BCRYPT_MAX_BYTES = 72  # bcrypt only reads 72 bytes; the bcrypt package raises ValueError beyond that
# Checked when the username is unknown, so that answer takes about as long as a wrong password.
# Cost 10 on purpose: it must match the cost of the stored hashes (the seed's, and any you make
# with bcrypt.gensalt(10)). A user hashed at another cost would make the two answers differ in time.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equalizer-not-a-password", bcrypt.gensalt(10))


def find_user_by_id(conn: Connection, user_id: int) -> dict[str, Any] | None:
    """The session user: id, username, role, account_id (never the password hash)."""
    return conn.cursor(row_factory=dict_row).execute(SQL["find_user_by_id"], {"user_id": user_id}).fetchone()


def authenticate(pool: ConnectionPool, username: str, password: str) -> dict[str, Any] | None:
    """The session user for a correct username + password, else None (same answer for both mistakes)."""
    secret = password.encode("utf-8")
    if len(secret) > BCRYPT_MAX_BYTES:
        return None  # no stored hash can match it
    # Its own short connection, given back BEFORE the slow bcrypt check (about 45 ms at cost 10),
    # so a stream of login attempts never holds the pool's connections.
    with pool.connection() as conn:
        row = conn.cursor(row_factory=dict_row).execute(
            SQL["find_user_by_username"], {"username": username}
        ).fetchone()
    stored = row["password_hash"].encode("utf-8") if row else _DUMMY_HASH
    if not bcrypt.checkpw(secret, stored) or row is None:
        return None
    return {field: row[field] for field in SESSION_USER_FIELDS}

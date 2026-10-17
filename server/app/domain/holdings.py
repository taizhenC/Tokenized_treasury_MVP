"""Holdings screen and account directory (Lesson 5). The SESSION user decides which statement runs."""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from app import queries

SQL = queries.load("holdings", queries.HOLDINGS_BLOCKS)

Row = dict[str, Any]


def holdings_for(conn: Connection, user: Row) -> list[Row]:
    """PARTICIPANT: only their own account. AUDITOR: every account."""
    cur = conn.cursor(row_factory=dict_row)
    if user["role"] == "AUDITOR":
        return cur.execute(SQL["holdings_all"]).fetchall()
    return cur.execute(SQL["holdings_for_account"], {"account_id": user["account_id"]}).fetchall()


def list_accounts(conn: Connection) -> list[Row]:
    """Every account, no balances: the transfer form's "to" dropdown."""
    return conn.cursor(row_factory=dict_row).execute(SQL["list_accounts"]).fetchall()

"""Operation history (Lesson 10). The SESSION user decides which statement runs."""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from app import queries

SQL = queries.load("history", queries.HISTORY_BLOCKS)

Row = dict[str, Any]


def history_for(conn: Connection, user: Row) -> list[Row]:
    """PARTICIPANT: operations that touched their account. AUDITOR: every operation. Newest id first."""
    cur = conn.cursor(row_factory=dict_row)
    if user["role"] == "AUDITOR":
        return cur.execute(SQL["history_all"]).fetchall()
    return cur.execute(SQL["history_for_account"], {"account_id": user["account_id"]}).fetchall()

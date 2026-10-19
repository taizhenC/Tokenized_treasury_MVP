"""Operation history (Lesson 10). The SESSION user decides which statement runs."""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from app import queries

SQL = queries.load("history", queries.HISTORY_BLOCKS)

Row = dict[str, Any]

# The roles that read every row. Anything else reads by its own account_id - deny by default:
# a user without an account matches no row.
OVERSEERS = ("AUDITOR", "OPERATOR")


def history_for(conn: Connection, user: Row) -> list[Row]:
    """PARTICIPANT: operations that touched their account. AUDITOR / OPERATOR: every operation.

    Newest id first. Settlement payments and the day steps have no asset (symbol and quantity are
    None); amount_cents is set only for the cash-leg operations.
    """
    cur = conn.cursor(row_factory=dict_row)
    if user["role"] in OVERSEERS:
        return cur.execute(SQL["history_all"]).fetchall()
    return cur.execute(SQL["history_for_account"], {"account_id": user["account_id"]}).fetchall()

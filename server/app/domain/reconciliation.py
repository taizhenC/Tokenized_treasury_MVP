"""Reconciliation (Lesson 9): two independent checks, read in ONE snapshot."""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from app import queries
from app.domain.transactions import transaction

SQL = queries.load("reconciliation", queries.RECONCILIATION_BLOCKS)


def run_report(conn: Connection) -> dict[str, Any]:
    """Both checks inside one REPEATABLE READ, READ ONLY transaction on `conn`.

    set_snapshot must be the first statement of the transaction (Postgres refuses it later with
    25001), and it only changes THIS transaction: the pooled connection goes back at READ COMMITTED.
    """
    with transaction(conn):
        cur = conn.cursor(row_factory=dict_row)
        cur.execute(SQL["set_snapshot"])
        conservation = cur.execute(SQL["check_conservation"]).fetchall()
        buckets = cur.execute(SQL["check_positions_vs_ledger"]).fetchall()
    # `is True`: an ok that came back NULL (a missing COALESCE) counts as a failure.
    passed = all(row["ok"] is True for row in [*conservation, *buckets])
    return {"passed": passed, "conservation": conservation, "positionsVsLedger": buckets}

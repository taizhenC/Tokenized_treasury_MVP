"""One connection, one real transaction - and the rowcount rule.

psycopg sends BEGIN / COMMIT / ROLLBACK for us inside `with conn.transaction():`. That is why the
query files have no begin/commit/rollback blocks: a raw "COMMIT" sent with execute() would end
the transaction behind psycopg's back, and a later ROLLBACK could no longer undo the first half.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection, Cursor
from psycopg.pq import TransactionStatus


class RowCountError(RuntimeError):
    """A statement changed a different number of rows than the flow requires (-> 500)."""


@contextmanager
def transaction(conn: Connection) -> Iterator[None]:
    """BEGIN ... COMMIT around the block, or ROLLBACK if anything in it raises.

    It must be the first thing done on the connection. If any statement ran before it, psycopg
    has already opened a transaction implicitly, conn.transaction() would only be a SAVEPOINT, and
    the real COMMIT would happen later, when the pool takes the connection back - after the
    response is decided. Refuse that instead of silently getting it wrong.
    """
    status = conn.info.transaction_status
    if status != TransactionStatus.IDLE:
        raise RuntimeError(
            f"transaction() needs an idle connection, but it is {status.name}: "
            "start the transaction before running any statement on this connection"
        )
    with conn.transaction():
        yield


def expect_rowcount(cur: Cursor, expected: int, statement: str) -> None:
    """An UPDATE that matches no row is NOT an SQL error - it just changes nothing. Make it one."""
    if cur.rowcount != expected:
        raise RowCountError(f"{statement}: rowcount {cur.rowcount}, expected {expected}")

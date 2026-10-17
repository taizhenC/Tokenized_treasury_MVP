"""Transfers (Lesson 7, idempotency from Lesson 8): move TOKENIZED units to another approved account."""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import queries
from app.domain.idempotency import request_hash, reserve
from app.domain.outcome import Outcome, RollbackAndRespond, reject
from app.domain.transactions import expect_rowcount, transaction

SQL = queries.load("transfer", queries.TRANSFER_BLOCKS)


def transfer(
    conn: Connection,
    *,
    actor_user_id: int,
    source_account_id: int,
    destination_account_id: int,
    asset_id: int,
    quantity: int,
    idempotency_key: str,
) -> Outcome:
    """Run one transfer in one transaction on `conn` (a connection nothing has used yet).

    The caller has already checked the role, taken source_account_id from the session and
    rejected a transfer to yourself; the database CHECKs are the backstop for all three.
    """
    params: dict[str, Any] = {
        "actor_user_id": actor_user_id,
        "source_account_id": source_account_id,
        "destination_account_id": destination_account_id,
        "asset_id": asset_id,
        "quantity": quantity,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash("TRANSFER", source_account_id, destination_account_id, asset_id, quantity),
    }
    try:
        with transaction(conn):
            cur = conn.cursor(row_factory=dict_row)
            # ForeignKeyViolation here (unknown destination) propagates -> ROLLBACK -> 422.
            params["operation_id"] = reserve(cur, SQL, params)

            # Both rows, locked lowest account_id first. They come back in account_id order,
            # NOT source first, so look the source up by its id.
            locked = cur.execute(SQL["lock_positions"], params).fetchall()
            if len(locked) != 2:
                raise RuntimeError(f"expected 2 position rows, locked {len(locked)}")
            by_account = {row["account_id"]: row for row in locked}
            if by_account[source_account_id]["tokenized_units"] < quantity:
                raise reject(422, "insufficient tokenized units")

            approvals = cur.execute(SQL["read_approvals"], params).fetchall()
            if len(approvals) != 2 or not all(row["is_approved"] is True for row in approvals):
                raise reject(422, "wallet is not approved")  # deny by default: a missing row fails too

            cur.execute(SQL["debit_source"], params)
            expect_rowcount(cur, 1, "debit_source")
            source_after = cur.fetchone()["tokenized_units"]
            cur.execute(SQL["credit_destination"], params)
            expect_rowcount(cur, 1, "credit_destination")
            # The recipient's new balance is NOT put in the result: a participant may see only
            # their own account (the result is also stored and replayed to the sender).
            cur.execute(SQL["insert_ledger_entries"], params)
            expect_rowcount(cur, 2, "insert_ledger_entries")

            result = {
                "operationId": params["operation_id"],
                "type": "TRANSFER",
                "sourceAccountId": source_account_id,
                "destinationAccountId": destination_account_id,
                "quantity": quantity,
                "sourceTokenizedUnits": source_after,
            }
            cur.execute(SQL["store_result"], {**params, "result": Jsonb(result)})
            expect_rowcount(cur, 1, "store_result")
        return Outcome(201, result)
    except RollbackAndRespond as stop:
        return stop.outcome

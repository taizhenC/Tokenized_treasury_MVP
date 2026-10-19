"""Deliver orders (Lesson 12): a valued DVP delivery that settles now or recycles (waits).

One request, one transaction (deliver_order.sql + settlement_engine.sql):
    reserve the key -> engine.lock -> phase must be OPEN -> check the risk controls
    pass: settle, record the instruction SETTLED, retry the waiting orders (this credit may free them) -> 201
    fail: record the instruction RECYCLING with the reason (nothing changed, so no retry)       -> 202
"""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import queries
from app.domain import risk, settlement_engine
from app.domain.idempotency import request_hash, reserve
from app.domain.outcome import Outcome, RollbackAndRespond, reject
from app.domain.transactions import expect_rowcount, transaction

SQL = queries.load("deliver_order", queries.DELIVER_ORDER_BLOCKS)


def deliver(
    conn: Connection,
    *,
    actor_user_id: int,
    deliverer_account_id: int,
    receiver_account_id: int,
    asset_id: int,
    quantity: int,
    amount_cents: int,
    idempotency_key: str,
) -> Outcome:
    """Place one deliver order in one transaction on `conn` (a connection nothing has used yet).

    The caller has already checked the role, taken deliverer_account_id from the session and
    rejected a delivery to yourself; the database CHECKs are the backstop for all three.
    """
    params: dict[str, Any] = {
        "actor_user_id": actor_user_id,
        "deliverer_account_id": deliverer_account_id,
        "receiver_account_id": receiver_account_id,
        "asset_id": asset_id,
        "quantity": quantity,
        "amount_cents": amount_cents,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash(
            "DELIVER_ORDER", deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents
        ),
    }
    try:
        with transaction(conn):
            cur = conn.cursor(row_factory=dict_row)
            # ForeignKeyViolation here (unknown receiver) propagates -> ROLLBACK -> 422 "unknown account".
            params["operation_id"] = reserve(cur, SQL, params)

            engine = settlement_engine.lock(cur, asset_id)
            if engine.phase != "OPEN":
                raise reject(422, settlement_engine.VALUED_PROCESSING_CLOSED)

            instruction = {
                "submitted_operation_id": params["operation_id"],
                "deliverer_account_id": deliverer_account_id,
                "receiver_account_id": receiver_account_id,
                "asset_id": asset_id,
                "quantity": quantity,
                "amount_cents": amount_cents,
            }
            # The controls, on the rows this transaction holds locked (app/domain/risk.py).
            reason = settlement_engine.evaluate(engine, instruction)
            if reason is None:
                status, settlement_operation_id = "SETTLED", settlement_engine.settle(cur, engine, instruction)
            else:
                status, settlement_operation_id = "RECYCLING", None
            # The EXACT reason is stored; only the answer below is masked for the deliverer.
            instruction_id = cur.execute(
                SQL["insert_delivery_instruction"],
                {
                    **params,
                    "submitted_operation_id": params["operation_id"],
                    "business_date": engine.business_date,
                    "status": status,
                    "recycle_reason": reason,
                    "settlement_operation_id": settlement_operation_id,
                },
            ).fetchone()["id"]
            # Only a settlement is a credit event; a recycled order changed nothing, so nothing new can pass.
            released = settlement_engine.retry_recycling(cur, engine) if status == "SETTLED" else []

            result = {
                "operationId": params["operation_id"],
                "type": "DELIVER_ORDER",
                "instructionId": instruction_id,
                "businessDate": engine.business_date.isoformat(),
                "fromAccountId": deliverer_account_id,
                "toAccountId": receiver_account_id,
                "quantity": quantity,
                "amountCents": amount_cents,
                "status": status,
                "recycleReason": risk.reason_for_viewer(
                    reason,
                    viewer_account_id=deliverer_account_id,
                    deliverer_account_id=deliverer_account_id,
                    receiver_account_id=receiver_account_id,
                ),
                "settlementOperationId": settlement_operation_id,
                # Only the orders the deliverer took part in: it may not learn about the others.
                "releasedInstructionIds": settlement_engine.visible_releases(released, deliverer_account_id),
                # The deliverer's own figures after the whole transaction (the receiver's stay private).
                "account": settlement_engine.account_summary(engine, deliverer_account_id),
            }
            cur.execute(SQL["store_result"], {**params, "result": Jsonb(result)})
            expect_rowcount(cur, 1, "store_result")
        # The block ended without an exception: psycopg has sent COMMIT.
        return Outcome(201 if status == "SETTLED" else 202, result)
    except RollbackAndRespond as stop:
        return stop.outcome

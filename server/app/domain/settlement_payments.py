"""Settlement progress payments (Lesson 12): Fed funds a participant wires in to reduce its debit.

One request, one transaction (settlement_payment.sql + settlement_engine.sql):
    reserve the key -> engine.lock -> phase must be OPEN -> credit the settlement balance ->
    two cash lines -> retry the waiting orders (this credit may be what one waits for) -> 201

Demo step 5: gamma (net -1,000,000) pays 50,000 -> net -950,000, and alpha's waiting order #3 to
gamma now passes gamma's collateral monitor (206 x 9,500 - 1,950,000 = 7,000) and settles.
"""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import queries
from app.domain import settlement_engine
from app.domain.idempotency import request_hash, reserve
from app.domain.outcome import Outcome, RollbackAndRespond, reject
from app.domain.transactions import expect_rowcount, transaction

SQL = queries.load("settlement_payment", queries.SETTLEMENT_PAYMENT_BLOCKS)


def pay(
    conn: Connection,
    *,
    actor_user_id: int,
    account_id: int,
    asset_id: int,
    amount_cents: int,
    idempotency_key: str,
) -> Outcome:
    """Make one settlement progress payment in one transaction on `conn` (a connection nothing has used yet).

    The caller has already checked the role and taken account_id from the session. asset_id is
    the instrument whose waiting orders the engine may settle (it locks that asset's positions).
    """
    params: dict[str, Any] = {
        "actor_user_id": actor_user_id,
        "account_id": account_id,
        "amount_cents": amount_cents,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash("SETTLEMENT_PROGRESS_PAYMENT", account_id, amount_cents),
    }
    try:
        with transaction(conn):
            cur = conn.cursor(row_factory=dict_row)
            params["operation_id"] = reserve(cur, SQL, params)

            engine = settlement_engine.lock(cur, asset_id)
            if engine.phase != "OPEN":
                raise reject(422, settlement_engine.VALUED_PROCESSING_CLOSED)
            params["business_date"] = engine.business_date

            cur.execute(SQL["credit_settlement_payment"], params)
            expect_rowcount(cur, 1, "credit_settlement_payment")
            credited = cur.fetchone()
            cur.execute(SQL["insert_payment_cash_entries"], params)
            expect_rowcount(cur, 2, "insert_payment_cash_entries")
            # The engine's copy must see the credit before it retries the waiting orders.
            settlement_engine.refresh_settlement_account(engine, credited)
            released = settlement_engine.retry_recycling(cur, engine)

            result = {
                "operationId": params["operation_id"],
                "type": "SETTLEMENT_PROGRESS_PAYMENT",
                "accountId": account_id,
                "amountCents": amount_cents,
                "businessDate": engine.business_date.isoformat(),
                # Only the orders this account took part in: it may not learn about the others.
                "releasedInstructionIds": settlement_engine.visible_releases(released, account_id),
                "account": settlement_engine.account_summary(engine, account_id),
            }
            cur.execute(SQL["store_result"], {**params, "result": Jsonb(result)})
            expect_rowcount(cur, 1, "store_result")
        return Outcome(201, result)
    except RollbackAndRespond as stop:
        return stop.outcome

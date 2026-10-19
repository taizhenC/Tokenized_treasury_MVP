"""POST /api/delivery-orders and POST /api/settlement-progress-payments (Lesson 12).

Role check (dependency) -> body validation (Pydantic, 400) -> one pooled connection ->
one domain call = one transaction -> the outcome as JSON. A deliver order answers 201 (SETTLED)
or 202 (RECYCLING: accepted, it waits); a payment answers 201. Database errors raised inside the
domain call are mapped by app.errors (422 / 409 / 503 / 500) after psycopg rolled back.
(GET /api/delivery-orders is a settlement read: app/routes/settlement_reads.py.)
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.deps import DbPool, Participant
from app.domain import assets, delivery_orders, settlement_payments
from app.responses import outcome_response
from app.schemas import DeliverOrderRequest, SettlementPaymentRequest

router = APIRouter(prefix="/api", tags=["settlement"])


@router.post("/delivery-orders", status_code=201)
def create_delivery_order(body: DeliverOrderRequest, user: Participant, db: DbPool) -> JSONResponse:
    if body.to_account_id == user["account_id"]:
        # CHECKs operations_no_self_transfer and delivery_no_self_delivery are the backstops.
        raise HTTPException(400, "cannot deliver to yourself")
    asset_id = assets.demo_asset_id(db)
    with db.connection() as conn:
        outcome = delivery_orders.deliver(
            conn,
            actor_user_id=user["id"],
            deliverer_account_id=user["account_id"],  # from the session, never from the body
            receiver_account_id=body.to_account_id,
            asset_id=asset_id,
            quantity=body.quantity,
            amount_cents=body.amount_cents,
            idempotency_key=body.idempotency_key,
        )
    return outcome_response(outcome)


@router.post("/settlement-progress-payments", status_code=201)
def create_settlement_progress_payment(body: SettlementPaymentRequest, user: Participant, db: DbPool) -> JSONResponse:
    asset_id = assets.demo_asset_id(db)
    with db.connection() as conn:
        outcome = settlement_payments.pay(
            conn,
            actor_user_id=user["id"],
            account_id=user["account_id"],  # from the session, never from the body
            asset_id=asset_id,
            amount_cents=body.amount_cents,
            idempotency_key=body.idempotency_key,
        )
    return outcome_response(outcome)

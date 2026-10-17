"""POST /api/conversions (Lesson 6; idempotent from Lesson 8).

Role check (dependency) -> body validation (Pydantic, 400) -> one pooled connection ->
one domain call = one transaction -> the outcome as JSON. Database errors raised inside the
domain call are mapped by app.errors (422 / 409 / 503 / 500) after psycopg rolled back.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.deps import DbPool, Participant
from app.domain import assets, conversions
from app.responses import outcome_response
from app.schemas import ConversionRequest

router = APIRouter(prefix="/api", tags=["conversions"])


@router.post("/conversions", status_code=201)
def create_conversion(body: ConversionRequest, user: Participant, db: DbPool) -> JSONResponse:
    asset_id = assets.demo_asset_id(db)
    with db.connection() as conn:
        outcome = conversions.convert(
            conn,
            direction=body.direction,
            actor_user_id=user["id"],
            account_id=user["account_id"],  # from the session, never from the body
            asset_id=asset_id,
            quantity=body.quantity,
            idempotency_key=body.idempotency_key,
        )
    return outcome_response(outcome)

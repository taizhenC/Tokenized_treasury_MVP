"""POST /api/settlement-day/valued-cutoff and /api/settlement-day/final-settlement (Lesson 13).

Operator only (the simulated DTC settlement desk). The body carries only the idempotency key:
which day is cut off or closed always comes from the database, never from the request.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.deps import DbPool, Operator
from app.domain import settlement_day
from app.responses import outcome_response
from app.schemas import SettlementDayRequest

router = APIRouter(prefix="/api", tags=["settlement-day"])


@router.post("/settlement-day/valued-cutoff", status_code=201)
def run_valued_cutoff(body: SettlementDayRequest, user: Operator, db: DbPool) -> JSONResponse:
    with db.connection() as conn:
        outcome = settlement_day.valued_cutoff(
            conn, actor_user_id=user["id"], idempotency_key=body.idempotency_key
        )
    return outcome_response(outcome)


@router.post("/settlement-day/final-settlement", status_code=201)
def run_final_settlement(body: SettlementDayRequest, user: Operator, db: DbPool) -> JSONResponse:
    with db.connection() as conn:
        outcome = settlement_day.final_settlement(
            conn, actor_user_id=user["id"], idempotency_key=body.idempotency_key
        )
    return outcome_response(outcome)

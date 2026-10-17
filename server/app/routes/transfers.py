"""POST /api/transfers (Lesson 7; idempotent from Lesson 8)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.deps import DbPool, Participant
from app.domain import assets, transfers
from app.responses import outcome_response
from app.schemas import TransferRequest

router = APIRouter(prefix="/api", tags=["transfers"])


@router.post("/transfers", status_code=201)
def create_transfer(body: TransferRequest, user: Participant, db: DbPool) -> JSONResponse:
    if body.to_account_id == user["account_id"]:
        raise HTTPException(400, "cannot transfer to yourself")  # CHECK operations_no_self_transfer is the backstop
    asset_id = assets.demo_asset_id(db)
    with db.connection() as conn:
        outcome = transfers.transfer(
            conn,
            actor_user_id=user["id"],
            source_account_id=user["account_id"],  # from the session, never from the body
            destination_account_id=body.to_account_id,
            asset_id=asset_id,
            quantity=body.quantity,
            idempotency_key=body.idempotency_key,
        )
    return outcome_response(outcome)

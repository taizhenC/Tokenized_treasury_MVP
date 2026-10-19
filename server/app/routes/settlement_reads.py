"""GET /api/settlement/day, /api/settlement/accounts, /api/settlement/closed-days, /api/delivery-orders (Lesson 14).

Every view comes from the SESSION user: a participant sees only its own account and the orders it
delivers or receives; the auditor and the operator see everything. Query parameters are ignored.
"""

from typing import Any

from fastapi import APIRouter

from app.deps import CurrentUser, DbPool, Overseer
from app.domain import assets, settlement_reads

router = APIRouter(prefix="/api", tags=["settlement"])


@router.get("/settlement/day")
def get_settlement_day(user: CurrentUser, db: DbPool) -> dict[str, Any]:
    """Anyone signed in: the business date, its phase, the maximum net debit cap and the collateral price."""
    asset_id = assets.demo_asset_id(db)
    with db.connection() as conn:
        return settlement_reads.settlement_day(conn, asset_id)


@router.get("/settlement/accounts")
def get_settlement_accounts(user: CurrentUser, db: DbPool) -> list[dict[str, Any]]:
    """A participant: a list with only its own settlement account. Auditor / operator: every account.
    Net, collateral value, collateral monitor, net debit cap and headroom, in cents."""
    asset_id = assets.demo_asset_id(db)
    with db.connection() as conn:
        return settlement_reads.settlement_accounts_for(conn, user, asset_id)


@router.get("/settlement/closed-days")
def get_closed_days(user: Overseer, db: DbPool) -> list[dict[str, Any]]:
    """Auditor / operator: the 10 most recent closed days (figures + NSS file), newest first.
    The figures list every account, so a participant may not read them."""
    with db.connection() as conn:
        return settlement_reads.closed_days(conn)


@router.get("/delivery-orders")
def get_delivery_orders(user: CurrentUser, db: DbPool) -> list[dict[str, Any]]:
    """A participant: orders it delivers or receives, with the other side's recycle reasons masked.
    Auditor / operator: every order with its exact reason. Newest id first, at most 100."""
    with db.connection() as conn:
        return settlement_reads.delivery_instructions_for(conn, user)

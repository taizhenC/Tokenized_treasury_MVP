"""GET /api/reconciliation (Lesson 9)."""

from typing import Any

from fastapi import APIRouter

from app.deps import Auditor, DbPool
from app.domain import reconciliation

router = APIRouter(prefix="/api", tags=["reconciliation"])


@router.get("/reconciliation")
def get_reconciliation(user: Auditor, db: DbPool) -> dict[str, Any]:
    """Auditor only: check (2) lists every account's balances, and a participant may only see
    their own. A read-only report, so a GET."""
    with db.connection() as conn:
        return reconciliation.run_report(conn)

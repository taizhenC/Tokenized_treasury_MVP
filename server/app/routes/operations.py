"""GET /api/operations (Lesson 10)."""

from fastapi import APIRouter

from app.deps import CurrentUser, DbPool, User
from app.domain import history

router = APIRouter(prefix="/api", tags=["operations"])


@router.get("/operations")
def get_operations(user: CurrentUser, db: DbPool) -> list[User]:
    """A participant sees operations that touched their account; the auditor and the operator see all.
    Newest id first, at most 100."""
    with db.connection() as conn:
        return history.history_for(conn, user)

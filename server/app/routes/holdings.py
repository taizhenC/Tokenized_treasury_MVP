"""GET /api/holdings and GET /api/accounts (Lesson 5)."""

from fastapi import APIRouter

from app.deps import CurrentUser, DbPool, User
from app.domain import holdings

router = APIRouter(prefix="/api", tags=["holdings"])


@router.get("/holdings")
def get_holdings(user: CurrentUser, db: DbPool) -> list[User]:
    """A participant sees only their own account; the auditor sees every account.
    Query parameters are ignored: the account always comes from the session."""
    with db.connection() as conn:
        return holdings.holdings_for(conn, user)


@router.get("/accounts")
def get_accounts(user: CurrentUser, db: DbPool) -> list[User]:
    """The account directory for the transfer form (no balances)."""
    with db.connection() as conn:
        return holdings.list_accounts(conn)

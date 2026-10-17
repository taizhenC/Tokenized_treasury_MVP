"""FastAPI dependencies: the connection pool and who is logged in.

Authorization is decided here, on the server, from the SESSION user row - never from anything
the browser sends:
    current_user  401 unless the signed session cookie names an existing user
    participant   403 unless that user is a PARTICIPANT (the only role that may change holdings)
    auditor       403 unless that user is an AUDITOR (read-only, sees every account)
"""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from psycopg_pool import ConnectionPool

from app.db import pool
from app.domain import auth

User = dict[str, Any]


def get_pool() -> ConnectionPool:
    """The connection pool, as a dependency so tests can swap it (app.dependency_overrides)."""
    return pool


DbPool = Annotated[ConnectionPool, Depends(get_pool)]


def current_user(request: Request, db: DbPool) -> User:
    user_id = request.session.get("user_id")
    if type(user_id) is not int:
        raise HTTPException(401, "log in first")
    # Its own short connection: a write flow must start its transaction on an unused connection.
    with db.connection() as conn:
        user = auth.find_user_by_id(conn, user_id)
    if user is None:
        raise HTTPException(401, "log in first")
    return user


def participant(user: Annotated[User, Depends(current_user)]) -> User:
    if user["role"] != "PARTICIPANT":
        raise HTTPException(403, "participants only")
    return user


def auditor(user: Annotated[User, Depends(current_user)]) -> User:
    if user["role"] != "AUDITOR":
        raise HTTPException(403, "auditors only")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
Participant = Annotated[User, Depends(participant)]
Auditor = Annotated[User, Depends(auditor)]

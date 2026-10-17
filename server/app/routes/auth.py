"""POST /api/login, POST /api/logout, GET /api/me (Lesson 5)."""

from fastapi import APIRouter, HTTPException, Request, Response

from app.deps import CurrentUser, DbPool, User
from app.domain import auth
from app.schemas import LoginRequest

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login")
def login(body: LoginRequest, request: Request, db: DbPool) -> User:
    user = auth.authenticate(db, body.username, body.password)
    if user is None:
        # Same answer for an unknown user and a wrong password.
        raise HTTPException(401, "invalid username or password")
    request.session.clear()
    request.session["user_id"] = user["id"]  # the only thing the cookie holds
    return user


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    request.session.clear()  # the response tells the browser to delete the cookie
    return Response(status_code=204)


@router.get("/me")
def me(user: CurrentUser) -> User:
    return user

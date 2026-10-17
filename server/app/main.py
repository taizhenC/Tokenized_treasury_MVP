from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import PoolTimeout
from starlette.middleware.sessions import SessionMiddleware

from app.db import pool
from app.errors import UnexpectedErrors, install_error_handlers
from app.limits import BodySizeLimit
from app.routes import auth, conversions, holdings, operations, reconciliation, transfers
from app.settings import Settings, load_settings

# The built React app (`npm run build` writes it). Present in the production image; absent in
# development, where Vite serves the UI and proxies /api here.
CLIENT_DIST = Path(__file__).resolve().parent.parent.parent / "client" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start filling the pool in the background, so the API still starts (and
    # can report the problem) when the database is down.
    pool.open()
    yield
    pool.close()


def health():
    """Report whether the API is up and can reach the database."""
    try:
        with pool.connection(timeout=2):
            pass
    except (PoolTimeout, psycopg.OperationalError):
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unreachable"})
    return {"status": "ok", "database": "ok"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    # Interactive API docs live under /api so they work through the Vite proxy too.
    app = FastAPI(
        title="Tokenized Treasury Lab API",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    # Login sessions: a cookie that holds only {"user_id": ...}, signed with SESSION_SECRET so the
    # browser can read it but not change it. Always HttpOnly (page JavaScript cannot read it);
    # SameSite=Lax (not sent on cross-site POSTs); Secure unless SESSION_COOKIE_SECURE=false.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="ttlab_session",
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    # Added after the session middleware, so it runs before it: an oversized body gets 413
    # before anything reads it.
    app.add_middleware(BodySizeLimit)
    # Added last, so it wraps everything: any unexpected exception becomes a clean 500.
    app.add_middleware(UnexpectedErrors)
    install_error_handlers(app)
    app.add_api_route("/api/health", health, methods=["GET"])
    # One router per lesson: L5 auth + holdings, L6 conversions, L7 transfers, L9 reconciliation, L10 operations.
    for module in (auth, holdings, conversions, transfers, reconciliation, operations):
        app.include_router(module.router)
    # Production: one service serves both the API and the UI from the same origin, so the
    # session cookie needs no CORS setup. Mounted last, so every /api route above wins.
    if CLIENT_DIST.is_dir():
        app.mount("/", StaticFiles(directory=CLIENT_DIST, html=True), name="client")
    return app


app = create_app()

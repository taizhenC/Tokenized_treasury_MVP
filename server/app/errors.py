"""Turn exceptions into JSON error responses. Every error body has the shape {"error": "..."}.

Database errors (psycopg) map to HTTP codes by class, after psycopg has already rolled the
transaction back:

    ForeignKeyViolation (23503)   -> 422  e.g. transfer to an account that does not exist
    CheckViolation (23514)        -> 422  a CHECK constraint caught what the app missed (the two
                                          risk-control CHECKs get their own message)
    UniqueViolation (23505)       -> 409  a row with that key already exists
    OperationalError              -> 503  connection lost (57P01, 08xxx), pool timeout,
                                          deadlock or serialization failure (40xxx): retry
    IdleInTransactionSessionTimeout (25P03) -> 503
    anything else                 -> 500
"""

import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import errors as pg
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("ttlab")

UNKNOWN_ACCOUNT_CONSTRAINTS = {
    "operations_source_account_id_fkey",
    "operations_destination_account_id_fkey",
}

# The settlement risk controls are CHECKs on settlement_accounts. The app checks them first and
# recycles the delivery with a clear reason, so these fire only when the app's own check missed a
# breach - the database is the last line of defence, and the answer still says which control held.
CONSTRAINT_MESSAGES = {
    "settlement_within_net_debit_cap": "net debit cap exceeded",
    "settlement_collateral_monitor_nonnegative": "collateral monitor would go negative",
}

# The 400 message for each request field (by its JSON name).
FIELD_MESSAGES = {
    "quantity": "quantity must be a whole number 1..1000000",
    "amountCents": "amountCents must be a whole number of cents 1..10000000000000",
    "toAccountId": "toAccountId must be a whole number 1..2147483647",
    "idempotencyKey": "idempotency key must be 1..100 characters (no NUL)",
    "direction": "direction must be TO_TOKENIZED or TO_TRADITIONAL",
    "username": "username and password are required",
    "password": "username and password are required",
}


def classify_db_error(exc: psycopg.Error) -> tuple[int, dict]:
    """The HTTP status and JSON body for a database error."""
    code = exc.sqlstate or ""
    constraint = exc.diag.constraint_name
    if isinstance(exc, pg.ForeignKeyViolation):
        message = "unknown account" if constraint in UNKNOWN_ACCOUNT_CONSTRAINTS else "unknown reference"
        return 422, {"error": message, "pgCode": code, "constraint": constraint}
    if isinstance(exc, pg.CheckViolation):
        message = CONSTRAINT_MESSAGES.get(constraint, "rule violated")
        return 422, {"error": message, "pgCode": code, "constraint": constraint}
    if isinstance(exc, pg.UniqueViolation):
        return 409, {"error": "conflicts with an existing row", "pgCode": code, "constraint": constraint}
    if isinstance(exc, (psycopg.OperationalError, pg.IdleInTransactionSessionTimeout)):
        return 503, {"error": "database unavailable, retry", "pgCode": code}
    return 500, {"error": "database error", "pgCode": code}


async def _database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
    status, body = classify_db_error(exc)
    if status >= 500:
        log.warning("%s %s -> %s: %s: %s", request.method, request.url.path, status, type(exc).__name__, exc)
    return JSONResponse(body, status_code=status)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic rejected the body: answer 400 (not FastAPI's default 422, which means a business rule here)."""
    first = exc.errors()[0]
    loc = first.get("loc", ())
    field = loc[-1] if len(loc) > 1 else None
    if first.get("type") == "extra_forbidden":
        message = f"unexpected field: {field}"
    elif first.get("type") == "json_invalid":
        message = "request body must be valid JSON"
    elif field in FIELD_MESSAGES:
        message = FIELD_MESSAGES[field]
    else:
        message = "request body must be a JSON object with the documented fields"
    return JSONResponse({"error": message}, status_code=400)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)


class UnexpectedErrors:
    """Pure ASGI middleware: any unhandled exception becomes 500 {"error": "internal error"}.

    Starlette's own catch-all sends the 500 and then re-raises, and uvicorn answers that by
    closing the connection, although the 500 never said "Connection: close". A keep-alive client
    then reads from or sends into a closing socket: on Linux (uvloop or asyncio) this lost
    responses and reset connections. Answering here, without re-raising, keeps the connection a
    normal keep-alive connection.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if started:
                raise  # part of a response already went out; only the server can end it now
            log.error("%s %s -> 500", scope["method"], scope["path"], exc_info=exc)
            await JSONResponse({"error": "internal error"}, status_code=500)(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(psycopg.Error, _database_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    # Every other exception is answered by the UnexpectedErrors middleware (see main.py).

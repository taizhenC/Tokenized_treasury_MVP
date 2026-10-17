"""Turn exceptions into JSON error responses. Every error body has the shape {"error": "..."}.

Database errors (psycopg) map to HTTP codes by class, after psycopg has already rolled the
transaction back:

    ForeignKeyViolation (23503)   -> 422  e.g. transfer to an account that does not exist
    CheckViolation (23514)        -> 422  a CHECK constraint caught what the app missed
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

log = logging.getLogger("ttlab")

UNKNOWN_ACCOUNT_CONSTRAINTS = {
    "operations_source_account_id_fkey",
    "operations_destination_account_id_fkey",
}

# The 400 message for each request field (by its JSON name).
FIELD_MESSAGES = {
    "quantity": "quantity must be a whole number 1..1000000",
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
        return 422, {"error": "rule violated", "pgCode": code, "constraint": constraint}
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


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    log.error("%s %s -> 500", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"error": "internal error"}, status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(psycopg.Error, _database_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)

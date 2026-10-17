"""Turn a domain Outcome into an HTTP response."""

from fastapi.responses import JSONResponse

from app.domain.outcome import Outcome


def outcome_response(outcome: Outcome) -> JSONResponse:
    """201 new, 200 replay (with an Idempotency-Replayed header), or the 409 / 422 error body."""
    headers = {"Idempotency-Replayed": "true"} if outcome.replayed else None
    return JSONResponse(outcome.body, status_code=outcome.status, headers=headers)

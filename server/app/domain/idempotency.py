"""Idempotency (Lesson 8): the same request sent twice takes effect once.

The unique constraint operations_actor_key_unique is the arbiter, so this also holds when the
two copies arrive at the same moment: the second INSERT waits on the index until the first
transaction ends, then gets 0 rows (first committed) or the row (first rolled back).
"""

import hashlib
from typing import Any

from psycopg import Cursor

from app.domain.outcome import RollbackAndRespond


def request_hash(*parts: object) -> str:
    """sha256 of a canonical string with the fields always in the same order, e.g.
    'TRANSFER|101|102|1|25'. Never hash the JSON body: its key order can change between retries."""
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def reserve(cur: Cursor[dict[str, Any]], sql: dict[str, str], params: dict[str, Any]) -> int:
    """Claim (actor, key) and return the new operation id - or end the request.

    Raises RollbackAndRespond with 200 + the stored result (same request again) or 409 (the key
    was used for a different request). Either way nothing was written, so the block rolls back.
    """
    row = cur.execute(sql["reserve_idempotency_key"], params).fetchone()
    if row is not None:
        return row["id"]
    existing = cur.execute(sql["find_existing_operation"], params).fetchone()
    if existing is None:
        raise RuntimeError("reserve_idempotency_key returned no row, but no operation holds the key")
    if existing["request_hash"] != params["request_hash"]:
        raise RollbackAndRespond(409, {"error": "idempotency key reused for a different request"})
    if existing["result"] is None:
        raise RuntimeError(f"operation {existing['id']} has no stored result to replay")
    stored = existing["result"]  # psycopg turns JSONB back into a dict
    raise RollbackAndRespond(200, stored, replayed=True)

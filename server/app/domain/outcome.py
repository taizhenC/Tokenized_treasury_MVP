"""What a write flow answers: an HTTP status and a JSON body."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Outcome:
    status: int
    body: dict[str, Any]
    replayed: bool = False  # True when the body is a stored result sent again (idempotent replay)


class RollbackAndRespond(Exception):
    """Raise inside `with transaction(conn):` to roll everything back and answer with this outcome.

    Used for every early exit: a replay (200), a reused key (409) and every rejection (422).
    """

    def __init__(self, status: int, body: dict[str, Any], *, replayed: bool = False) -> None:
        super().__init__(status, body)
        self.outcome = Outcome(status, body, replayed)


def reject(status: int, message: str) -> RollbackAndRespond:
    return RollbackAndRespond(status, {"error": message})

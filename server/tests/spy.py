"""A test-only stand-in for the connection pool that can act at a named SQL step.

The app takes its pool from the `get_pool` dependency, so a test can swap in a SpyPool with
app.dependency_overrides - nothing in the app knows about tests. The spy recognises the
app's statements by their text (each one is a "-- name:" block) and can:

    after={"debit_source": fn}        call fn(real_connection) right after that statement ran
    replace={"set_snapshot": sql}     send different SQL instead (contrast experiments only)
    rewrite_params={"debit_source": fn}   send fn(params) instead of params
    rewrite_rows={"lock_positions": fn}   hand the app fn(rows) instead of the real rows
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from app import queries

BLOCK_NAMES: dict[str, str] = {
    sql: name
    for stem, required in queries.ALL_FILES.items()
    for name, sql in queries.load(stem, required).items()
}

Hook = Callable[[Any], Any]


class SpyCursor:
    def __init__(self, cursor: Any, spy: "SpyPool", conn: Any) -> None:
        self._cursor = cursor
        self._spy = spy
        self._conn = conn
        self._name: str | None = None

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> "SpyCursor":
        name = BLOCK_NAMES.get(query) if isinstance(query, str) else None
        self._name = name
        if name in self._spy.replace:
            query = self._spy.replace[name]
            params = params if "%(" in query else None
        if name in self._spy.rewrite_params:
            params = self._spy.rewrite_params[name](params)
        self._cursor.execute(query, params, **kwargs)
        if name is not None:
            self._spy.seen.append(name)
        if name in self._spy.after:
            self._spy.after[name](self._conn)
        return self

    def fetchone(self) -> Any:
        if self._name in self._spy.rewrite_rows:
            rows = self._spy.rewrite_rows[self._name]([self._cursor.fetchone()])
            return rows[0]
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        rows = self._cursor.fetchall()
        if self._name in self._spy.rewrite_rows:
            return self._spy.rewrite_rows[self._name](rows)
        return rows

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._cursor, attr)


class SpyConnection:
    def __init__(self, conn: Any, spy: "SpyPool") -> None:
        self._conn = conn
        self._spy = spy

    def cursor(self, *args: Any, **kwargs: Any) -> SpyCursor:
        return SpyCursor(self._conn.cursor(*args, **kwargs), self._spy, self._conn)

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> SpyCursor:
        return self.cursor().execute(query, params, **kwargs)

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._conn, attr)


class SpyPool:
    def __init__(
        self,
        pool: Any,
        *,
        after: dict[str, Hook] | None = None,
        replace: dict[str, str] | None = None,
        rewrite_params: dict[str, Hook] | None = None,
        rewrite_rows: dict[str, Hook] | None = None,
    ) -> None:
        self._pool = pool
        self.after = after or {}
        self.replace = replace or {}
        self.rewrite_params = rewrite_params or {}
        self.rewrite_rows = rewrite_rows or {}
        self.seen: list[str] = []

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[SpyConnection]:
        with self._pool.connection(timeout) as conn:
            yield SpyConnection(conn, self)


@contextmanager
def spying(**hooks: Any) -> Iterator[SpyPool]:
    """Route every request of the live server through a SpyPool for the duration of the block."""
    from app.db import pool
    from app.deps import get_pool
    from app.main import app

    spy = SpyPool(pool, **hooks)
    app.dependency_overrides[get_pool] = lambda: spy
    try:
        yield spy
    finally:
        app.dependency_overrides.pop(get_pool, None)

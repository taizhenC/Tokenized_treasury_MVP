"""The one instrument of the MVP, looked up by symbol once and then remembered."""

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import queries

SQL = queries.load("holdings", queries.HOLDINGS_BLOCKS)
DEMO_SYMBOL = "DEMO-TREASURY"

_asset_id: int | None = None


def demo_asset_id(pool: ConnectionPool) -> int:
    """The id of DEMO-TREASURY. Uses its own short connection, never a request's write connection."""
    global _asset_id
    if _asset_id is None:
        with pool.connection() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                SQL["find_asset_by_symbol"], {"symbol": DEMO_SYMBOL}
            ).fetchone()
        if row is None:
            raise RuntimeError(f"asset {DEMO_SYMBOL} not found: run the seeds first (npm run db:seed)")
        _asset_id = row["id"]
    return _asset_id

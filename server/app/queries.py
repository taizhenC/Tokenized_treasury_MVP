"""The SQL statements the app runs, loaded from server/db/queries/*.sql.

Python never contains SQL text: every statement comes from a "-- name: ..." block.
Each domain module loads its own file once, at import:

    SQL = queries.load("transfer", TRANSFER_BLOCKS)
    cur.execute(SQL["lock_positions"], params)
"""

from functools import cache
from pathlib import Path

from app.sql import load_sql_blocks

QUERIES_DIR = Path(__file__).resolve().parent.parent / "db" / "queries"

AUTH_BLOCKS = ("find_user_by_username", "find_user_by_id")
HOLDINGS_BLOCKS = ("find_asset_by_symbol", "holdings_for_account", "holdings_all", "list_accounts")
CONVERSION_BLOCKS = (
    "reserve_idempotency_key",
    "find_existing_operation",
    "lock_position",
    "apply_conversion",
    "insert_ledger_entries",
    "store_result",
)
TRANSFER_BLOCKS = (
    "reserve_idempotency_key",
    "find_existing_operation",
    "lock_positions",
    "read_approvals",
    "debit_source",
    "credit_destination",
    "insert_ledger_entries",
    "store_result",
)
RECONCILIATION_BLOCKS = ("set_snapshot", "check_conservation", "check_positions_vs_ledger")
HISTORY_BLOCKS = ("history_for_account", "history_all")

# Every query file and the blocks the app needs from it (used by the tests to check them all).
ALL_FILES = {
    "auth": AUTH_BLOCKS,
    "holdings": HOLDINGS_BLOCKS,
    "convert_to_tokenized": CONVERSION_BLOCKS,
    "convert_to_traditional": CONVERSION_BLOCKS,
    "transfer": TRANSFER_BLOCKS,
    "reconciliation": RECONCILIATION_BLOCKS,
    "history": HISTORY_BLOCKS,
}


@cache
def load(file_stem: str, required: tuple[str, ...]) -> dict[str, str]:
    """Read server/db/queries/<file_stem>.sql, or say exactly which blocks are still missing."""
    path = QUERIES_DIR / f"{file_stem}.sql"
    if not path.exists():
        raise RuntimeError(f"server/db/queries/{path.name} does not exist yet.")
    blocks = load_sql_blocks(path)
    missing = [name for name in required if not blocks.get(name)]
    if missing:
        raise RuntimeError(f"server/db/queries/{path.name} is missing block(s): {', '.join(missing)}.")
    return blocks

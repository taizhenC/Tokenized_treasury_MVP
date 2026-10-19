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
    "lock_settlement_account",
    "read_collateral_price",
    "apply_conversion",
    "update_collateral",
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
RECONCILIATION_BLOCKS = (
    "set_snapshot",
    "check_conservation",
    "check_positions_vs_ledger",
    "check_cash_lines_net_to_zero",
    "check_settlement_vs_cash_ledger",
    "check_collateral_vs_positions",
    "check_settled_instructions_vs_ledgers",
    "check_risk_controls",
    "check_closed_days",
)
HISTORY_BLOCKS = ("history_for_account", "history_all")

# Lessons 12-14: the cash leg. The settlement engine's statements are shared by every flow that can
# settle or recycle a delivery (deliver order, settlement progress payment).
SETTLEMENT_ENGINE_BLOCKS = (
    "lock_settlement_day",
    "lock_engine_positions",
    "lock_settlement_accounts",
    "read_collateral_price",
    "insert_dvp_settlement_operation",
    "deliver_units",
    "receive_units",
    "credit_deliverer_settlement",
    "debit_receiver_settlement",
    "insert_dvp_ledger_entries",
    "insert_dvp_cash_entries",
    "list_recycling_instructions",
    "mark_instruction_settled",
    "update_recycle_reason",
)
DELIVER_ORDER_BLOCKS = (
    "reserve_idempotency_key",
    "find_existing_operation",
    "insert_delivery_instruction",
    "store_result",
)
SETTLEMENT_PAYMENT_BLOCKS = (
    "reserve_idempotency_key",
    "find_existing_operation",
    "credit_settlement_payment",
    "insert_payment_cash_entries",
    "store_result",
)
VALUED_CUTOFF_BLOCKS = (
    "reserve_idempotency_key",
    "find_existing_operation",
    "lock_settlement_day",
    "drop_recycling_instructions",
    "close_valued_processing",
    "store_result",
)
FINAL_SETTLEMENT_BLOCKS = (
    "reserve_idempotency_key",
    "find_existing_operation",
    "lock_settlement_day",
    "lock_settlement_accounts",
    "record_settlement_figures",
    "record_nss_file",
    "post_closing_participant_entries",
    "post_closing_fed_entries",
    "zero_settlement_accounts",
    "open_next_business_day",
    "store_result",
)
SETTLEMENT_READ_BLOCKS = (
    "settlement_day_summary",
    "settlement_account_for",
    "settlement_accounts_all",
    "delivery_instructions_for_account",
    "delivery_instructions_all",
    "closed_day_figures",
    "closed_day_nss_lines",
)

# Every query file and the blocks the app needs from it (used by the tests to check them all).
ALL_FILES = {
    "auth": AUTH_BLOCKS,
    "holdings": HOLDINGS_BLOCKS,
    "convert_to_tokenized": CONVERSION_BLOCKS,
    "convert_to_traditional": CONVERSION_BLOCKS,
    "transfer": TRANSFER_BLOCKS,
    "reconciliation": RECONCILIATION_BLOCKS,
    "history": HISTORY_BLOCKS,
    "settlement_engine": SETTLEMENT_ENGINE_BLOCKS,
    "deliver_order": DELIVER_ORDER_BLOCKS,
    "settlement_payment": SETTLEMENT_PAYMENT_BLOCKS,
    "valued_cutoff": VALUED_CUTOFF_BLOCKS,
    "final_settlement": FINAL_SETTLEMENT_BLOCKS,
    "settlement_reads": SETTLEMENT_READ_BLOCKS,
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

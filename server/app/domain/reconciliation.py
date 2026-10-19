"""Reconciliation (Lesson 9; settlement checks from Lesson 14): independent checks, read in ONE snapshot.

Units (Lesson 9):        conservation, positions vs. unit ledger.
Settlement (Lesson 14):  cash lines net to zero, settlement balances vs. cash ledger, collateral vs.
                         positions, settled deliver orders vs. both ledgers, risk controls, closed days.
"""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from app import queries
from app.domain.transactions import transaction

SQL = queries.load("reconciliation", queries.RECONCILIATION_BLOCKS)

# The report key of each settlement check, and the block that computes it (run in this order).
SETTLEMENT_CHECKS = (
    ("cashLinesNetToZero", "check_cash_lines_net_to_zero"),
    ("settlementVsCashLedger", "check_settlement_vs_cash_ledger"),
    ("collateralVsPositions", "check_collateral_vs_positions"),
    ("settledInstructionsVsLedgers", "check_settled_instructions_vs_ledgers"),
    ("riskControls", "check_risk_controls"),
    ("closedDays", "check_closed_days"),
)


def run_report(conn: Connection) -> dict[str, Any]:
    """Every check inside one REPEATABLE READ, READ ONLY transaction on `conn`.

    set_snapshot must be the first statement of the transaction (Postgres refuses it later with
    25001), and it only changes THIS transaction: the pooled connection goes back at READ COMMITTED.
    """
    with transaction(conn):
        cur = conn.cursor(row_factory=dict_row)
        cur.execute(SQL["set_snapshot"])
        conservation = cur.execute(SQL["check_conservation"]).fetchall()
        buckets = cur.execute(SQL["check_positions_vs_ledger"]).fetchall()
        settlement = {key: cur.execute(SQL[block]).fetchall() for key, block in SETTLEMENT_CHECKS}
    every_row = [*conservation, *buckets, *(row for rows in settlement.values() for row in rows)]
    # `is True`: an ok that came back NULL (a missing COALESCE) counts as a failure.
    passed = all(row["ok"] is True for row in every_row)
    return {"passed": passed, "conservation": conservation, "positionsVsLedger": buckets, **settlement}

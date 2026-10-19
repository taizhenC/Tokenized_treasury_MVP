"""Settlement reads (Lesson 14): the day, the settlement accounts, the deliver orders, the closed days.

The SESSION user decides which statement runs, never the request:
    PARTICIPANT          -> their own account only (and the orders they deliver or receive)
    AUDITOR or OPERATOR  -> every account and every order
Plain reads, no locks: a screen refresh never waits for the settlement engine.
"""

from datetime import date
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from app import queries
from app.domain import risk

SQL = queries.load("settlement_reads", queries.SETTLEMENT_READ_BLOCKS)

Row = dict[str, Any]

# The roles that read every account. Anything else reads by its own account_id - deny by default:
# a user without an account matches no row.
OVERSEERS = ("AUDITOR", "OPERATOR")


def settlement_day(conn: Connection, asset_id: int) -> Row:
    """The business date and phase, DTC's maximum net debit cap, and the collateral price."""
    row = conn.cursor(row_factory=dict_row).execute(SQL["settlement_day_summary"], {"asset_id": asset_id}).fetchone()
    if row is None:
        raise RuntimeError("no settlement day or collateral price: run the seeds first (npm run db:seed)")
    return row


def settlement_accounts_for(conn: Connection, user: Row, asset_id: int) -> list[Row]:
    """PARTICIPANT: a list with only their own settlement account. AUDITOR / OPERATOR: every account."""
    cur = conn.cursor(row_factory=dict_row)
    if user["role"] in OVERSEERS:
        return cur.execute(SQL["settlement_accounts_all"], {"asset_id": asset_id}).fetchall()
    params = {"account_id": user["account_id"], "asset_id": asset_id}
    return cur.execute(SQL["settlement_account_for"], params).fetchall()


def delivery_instructions_for(conn: Connection, user: Row) -> list[Row]:
    """Newest order first, at most 100.

    PARTICIPANT: the orders they deliver or receive, with a reason about the OTHER side masked as
    WAITING_ON_COUNTERPARTY (a lab choice: nobody learns another participant's limits from a
    recycle reason). AUDITOR / OPERATOR: every order with its exact reason.
    """
    cur = conn.cursor(row_factory=dict_row)
    if user["role"] in OVERSEERS:
        return cur.execute(SQL["delivery_instructions_all"]).fetchall()
    rows = cur.execute(SQL["delivery_instructions_for_account"], {"account_id": user["account_id"]}).fetchall()
    for row in rows:
        row["recycle_reason"] = risk.reason_for_viewer(
            row["recycle_reason"],
            viewer_account_id=user["account_id"],
            deliverer_account_id=row["deliverer_account_id"],
            receiver_account_id=row["receiver_account_id"],
        )
    return rows


def closed_days(conn: Connection) -> list[Row]:
    """The 10 most recent closed days, newest first: each account's final figure and the NSS file.

    The figures are read FIRST and decide which days are listed. A day that closes between the two
    reads has NSS lines but no figures in this answer, so its lines are left out; the next refresh
    shows it whole.
    """
    cur = conn.cursor(row_factory=dict_row)
    figures = cur.execute(SQL["closed_day_figures"]).fetchall()
    nss_lines = cur.execute(SQL["closed_day_nss_lines"]).fetchall()
    days: dict[date, Row] = {}  # a dict keeps insertion order: newest day first, as the rows arrive
    for row in figures:
        day = days.setdefault(
            row["business_date"], {"business_date": row["business_date"], "figures": [], "nss_file": []}
        )
        day["figures"].append(
            {
                "account_id": row["account_id"],
                "participant_name": row["participant_name"],
                "final_net_cents": row["final_net_cents"],
            }
        )
    for row in nss_lines:
        day = days.get(row["business_date"])
        if day is not None:
            day["nss_file"].append(
                {
                    "settling_bank_id": row["settling_bank_id"],
                    "bank_name": row["bank_name"],
                    "net_net_cents": row["net_net_cents"],
                }
            )
    return list(days.values())

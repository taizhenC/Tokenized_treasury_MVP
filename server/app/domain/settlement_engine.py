"""The settlement engine (Lesson 12): lock, settle, recycle and retry DVP deliveries.

It has no endpoint and no transaction of its own: a deliver order or a settlement progress payment
calls it inside ITS transaction, after reserving its idempotency key (settlement_engine.sql).

    engine = lock(cur, asset_id)        sequencer -> all positions -> all settlement accounts -> price
    settle(cur, engine, instruction)    one delivery: units, money, ledger lines (both legs or neither)
    retry_recycling(cur, engine)        settle every waiting order that now passes, until none does

The risk controls are checked in Python (app/domain/risk.py) on the rows this transaction holds
locked, so nobody can change them before COMMIT; the CHECKs on settlement_accounts are the backstop.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import Cursor

from app import queries
from app.domain import risk
from app.domain.risk import AccountState
from app.domain.transactions import expect_rowcount

SQL = queries.load("settlement_engine", queries.SETTLEMENT_ENGINE_BLOCKS)

# A lab choice: no delivery or payment once the valued cutoff has run (deliver orders and payments).
VALUED_PROCESSING_CLOSED = "valued processing is closed: the valued cutoff has passed"


@dataclass
class Engine:
    business_date: date
    phase: str  # 'OPEN' | 'VALUED_CUTOFF_PASSED'
    asset_id: int
    collateral_cents_per_unit: int
    accounts: dict[int, AccountState]  # every account, from the locked rows; kept current after each settle


def lock(cur: Cursor[dict[str, Any]], asset_id: int) -> Engine:
    """Take the engine's locks in THE global order and read what the controls need.

    The sequencer first (so engine requests run one at a time), then EVERY position of the asset,
    then EVERY settlement account: a retry may settle any account's order, and a lock taken later,
    in the middle of the transaction, could break the order and deadlock. With 4 accounts this is
    cheap; at DTC scale the work would be partitioned instead. Then the price (no lock: no
    endpoint changes it).
    """
    days = cur.execute(SQL["lock_settlement_day"]).fetchall()
    if len(days) != 1:
        raise RuntimeError(f"expected 1 settlement_day row, locked {len(days)}")
    day = days[0]
    params = {"asset_id": asset_id}
    # Rows come back in account_id order: look them up by account_id, never by position in the list.
    positions = {row["account_id"]: row for row in cur.execute(SQL["lock_engine_positions"], params).fetchall()}
    settlement_rows = cur.execute(SQL["lock_settlement_accounts"]).fetchall()
    price = cur.execute(SQL["read_collateral_price"], params).fetchone()
    if price is None:
        raise RuntimeError(f"no collateral price for asset {asset_id}")

    accounts: dict[int, AccountState] = {}
    for row in settlement_rows:
        position = positions.get(row["account_id"])
        if position is None:
            raise RuntimeError(f"settlement account {row['account_id']} has no position in asset {asset_id}")
        accounts[row["account_id"]] = _state(row, position["traditional_units"])
    return Engine(
        business_date=day["business_date"],
        phase=day["phase"],
        asset_id=asset_id,
        collateral_cents_per_unit=price["collateral_cents_per_unit"],
        accounts=accounts,
    )


def evaluate(engine: Engine, instruction: dict[str, Any]) -> str | None:
    """risk.evaluate_delivery on the engine's current copy of the two accounts (None = it may settle)."""
    return risk.evaluate_delivery(
        deliverer=_account(engine, instruction["deliverer_account_id"]),
        receiver=_account(engine, instruction["receiver_account_id"]),
        quantity=instruction["quantity"],
        amount_cents=instruction["amount_cents"],
        collateral_cents_per_unit=engine.collateral_cents_per_unit,
    )


def settle(cur: Cursor[dict[str, Any]], engine: Engine, instruction: dict[str, Any]) -> int:
    """Settle one delivery that passed the controls; return its DVP_SETTLEMENT operation id.

    instruction keys: submitted_operation_id, deliverer_account_id, receiver_account_id,
    asset_id, quantity, amount_cents. Both legs are written in the caller's transaction, so they
    commit together or not at all.
    """
    if instruction["asset_id"] != engine.asset_id:
        raise RuntimeError(f"the engine locked asset {engine.asset_id}, not {instruction['asset_id']}")
    deliverer_id = instruction["deliverer_account_id"]
    receiver_id = instruction["receiver_account_id"]
    _account(engine, deliverer_id)  # both must be accounts the engine locked
    _account(engine, receiver_id)
    params: dict[str, Any] = {
        "submitted_operation_id": instruction["submitted_operation_id"],
        "deliverer_account_id": deliverer_id,
        "receiver_account_id": receiver_id,
        "asset_id": instruction["asset_id"],
        "quantity": instruction["quantity"],
        "amount_cents": instruction["amount_cents"],
        "business_date": engine.business_date,
    }
    # A plain INSERT: a second settlement of the same order fails with UniqueViolation.
    params["settlement_operation_id"] = cur.execute(SQL["insert_dvp_settlement_operation"], params).fetchone()["id"]

    cur.execute(SQL["deliver_units"], params)
    expect_rowcount(cur, 1, "deliver_units")
    deliverer_units = cur.fetchone()["traditional_units"]
    cur.execute(SQL["receive_units"], params)
    expect_rowcount(cur, 1, "receive_units")
    receiver_units = cur.fetchone()["traditional_units"]

    cur.execute(SQL["credit_deliverer_settlement"], params)
    expect_rowcount(cur, 1, "credit_deliverer_settlement")
    deliverer_row = cur.fetchone()
    cur.execute(SQL["debit_receiver_settlement"], params)
    expect_rowcount(cur, 1, "debit_receiver_settlement")
    receiver_row = cur.fetchone()

    cur.execute(SQL["insert_dvp_ledger_entries"], params)
    expect_rowcount(cur, 2, "insert_dvp_ledger_entries")
    cur.execute(SQL["insert_dvp_cash_entries"], params)
    expect_rowcount(cur, 2, "insert_dvp_cash_entries")

    # The database's new values (RETURNING), not the app's arithmetic: the next check sees the truth.
    engine.accounts[deliverer_id] = _state(deliverer_row, deliverer_units)
    engine.accounts[receiver_id] = _state(receiver_row, receiver_units)
    return params["settlement_operation_id"]


def retry_recycling(cur: Cursor[dict[str, Any]], engine: Engine) -> list[dict[str, Any]]:
    """Settle every waiting order that passes now; return the settled ones in settle order.

    Called after a credit event (an order that settled, a payment - a lab choice: nothing else
    triggers a retry). One settlement can make room for another (it credits its deliverer), so the
    list is read again and passed over until a whole pass settles nothing. That ends: every
    pass that goes on settles at least one order, and there are only so many.
    Each returned row: {"id", "deliverer_account_id", "receiver_account_id"}.
    """
    settled: list[dict[str, Any]] = []
    while True:
        settled_this_pass = False
        waiting = cur.execute(SQL["list_recycling_instructions"], {"business_date": engine.business_date}).fetchall()
        for row in waiting:
            if row["asset_id"] != engine.asset_id:
                continue  # one instrument in this lab; another asset's orders would need its own engine
            reason = evaluate(engine, row)
            if reason is None:
                settlement_operation_id = settle(cur, engine, row)
                cur.execute(
                    SQL["mark_instruction_settled"],
                    {"instruction_id": row["id"], "settlement_operation_id": settlement_operation_id},
                )
                expect_rowcount(cur, 1, "mark_instruction_settled")
                settled.append({
                    "id": row["id"],
                    "deliverer_account_id": row["deliverer_account_id"],
                    "receiver_account_id": row["receiver_account_id"],
                })
                settled_this_pass = True
            elif reason != row["recycle_reason"]:
                cur.execute(SQL["update_recycle_reason"], {"instruction_id": row["id"], "recycle_reason": reason})
                expect_rowcount(cur, 1, "update_recycle_reason")
        if not settled_this_pass:
            return settled


def refresh_settlement_account(engine: Engine, row: dict[str, Any]) -> None:
    """Take one account's money figures from a settlement_accounts row just RETURNED (units unchanged)."""
    account_id = row["account_id"]
    engine.accounts[account_id] = _state(row, _account(engine, account_id).traditional_units)


def account_summary(engine: Engine, account_id: int) -> dict[str, int]:
    """One account's settlement figures for a result body (JSON-native ints only)."""
    state = _account(engine, account_id)
    return {
        "netCents": state.net_cents,
        "collateralValueCents": state.collateral_value_cents,
        "collateralMonitorCents": risk.collateral_monitor(state),
        "netDebitCapCents": state.net_debit_cap_cents,
        "traditionalUnits": state.traditional_units,
    }


def visible_releases(settled: list[dict[str, Any]], account_id: int) -> list[int]:
    """The ids of the settled orders this account took part in (it may not learn about the others)."""
    return [
        row["id"] for row in settled if account_id in (row["deliverer_account_id"], row["receiver_account_id"])
    ]


def _state(row: dict[str, Any], traditional_units: int) -> AccountState:
    return AccountState(
        account_id=row["account_id"],
        traditional_units=traditional_units,
        net_cents=row["net_cents"],
        collateral_value_cents=row["collateral_value_cents"],
        net_debit_cap_cents=row["net_debit_cap_cents"],
    )


def _account(engine: Engine, account_id: int) -> AccountState:
    state = engine.accounts.get(account_id)
    if state is None:
        raise RuntimeError(f"account {account_id} has no settlement account")
    return state

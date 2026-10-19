"""Conversions (Lesson 6, idempotency from Lesson 8, collateral from Lesson 12): mint or burn inside ONE account.

TO_TOKENIZED   traditional_units -= q, tokenized_units += q   (convert_to_tokenized.sql)
TO_TRADITIONAL tokenized_units -= q, traditional_units += q   (convert_to_traditional.sql)

Lesson 12: tokenized units have no collateral value, so a mint also lowers the account's
collateral value by q x the collateral price per unit, and a burn raises it again. A mint that
would push the Collateral Monitor (collateral + net) below zero is refused with a 422 at once (a lab
choice: it does not recycle). Lock order: the position row, then the settlement account - the same
order as the settlement engine, so a mint and a deliver order cannot deadlock.
"""

from dataclasses import dataclass
from typing import Any, Literal

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import queries
from app.domain import risk
from app.domain.idempotency import request_hash, reserve
from app.domain.outcome import Outcome, RollbackAndRespond, reject
from app.domain.transactions import expect_rowcount, transaction

Direction = Literal["TO_TOKENIZED", "TO_TRADITIONAL"]


@dataclass(frozen=True)
class _Kind:
    operation_type: str
    sql: dict[str, str]
    spends: str  # the column that must cover the quantity
    shortfall: str  # the 422 message when it does not
    removes_collateral: bool  # mint: the units stop counting as collateral, so the CM is checked first


KINDS: dict[str, _Kind] = {
    "TO_TOKENIZED": _Kind(
        "CONVERT_TO_TOKENIZED",
        queries.load("convert_to_tokenized", queries.CONVERSION_BLOCKS),
        "traditional_units",
        "insufficient traditional units",
        True,
    ),
    "TO_TRADITIONAL": _Kind(
        "CONVERT_TO_TRADITIONAL",
        queries.load("convert_to_traditional", queries.CONVERSION_BLOCKS),
        "tokenized_units",
        "insufficient tokenized units",
        False,
    ),
}


def convert(
    conn: Connection,
    *,
    direction: Direction,
    actor_user_id: int,
    account_id: int,
    asset_id: int,
    quantity: int,
    idempotency_key: str,
) -> Outcome:
    """Run one conversion in one transaction on `conn` (a connection nothing has used yet)."""
    kind = KINDS[direction]
    sql = kind.sql
    # One dict of NAMED parameters for every statement; each statement uses the names it needs.
    params: dict[str, Any] = {
        "actor_user_id": actor_user_id,
        "account_id": account_id,
        "asset_id": asset_id,
        "quantity": quantity,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash(kind.operation_type, account_id, asset_id, quantity),
    }
    try:
        with transaction(conn):
            cur = conn.cursor(row_factory=dict_row)
            params["operation_id"] = reserve(cur, sql, params)

            position = cur.execute(sql["lock_position"], params).fetchone()  # lock, THEN read
            if position is None:
                raise RuntimeError(f"no position row for account {account_id}, asset {asset_id}")
            if position[kind.spends] < quantity:
                raise reject(422, kind.shortfall)

            # Lesson 12: the settlement row, locked AFTER the position row (the one global lock order).
            account = cur.execute(sql["lock_settlement_account"], params).fetchone()
            if account is None:
                raise RuntimeError(f"no settlement account for account {account_id}")
            if kind.removes_collateral:
                price = cur.execute(sql["read_collateral_price"], params).fetchone()
                if price is None:
                    raise RuntimeError(f"no collateral price for asset {asset_id}")
                state = risk.AccountState(
                    account_id=account_id,
                    traditional_units=position["traditional_units"],
                    net_cents=account["net_cents"],
                    collateral_value_cents=account["collateral_value_cents"],
                    net_debit_cap_cents=account["net_debit_cap_cents"],
                )
                problem = risk.mint_problem(state, quantity, price["collateral_cents_per_unit"])
                if problem is not None:
                    raise reject(422, problem)

            cur.execute(sql["apply_conversion"], params)
            expect_rowcount(cur, 1, "apply_conversion")
            balances = cur.fetchone()
            # The units leave (mint) or rejoin (burn) the collateral value. The result body does not change.
            cur.execute(sql["update_collateral"], params)
            expect_rowcount(cur, 1, "update_collateral")
            cur.execute(sql["insert_ledger_entries"], params)
            expect_rowcount(cur, 2, "insert_ledger_entries")

            result = {
                "operationId": params["operation_id"],
                "type": kind.operation_type,
                "accountId": account_id,
                "quantity": quantity,
                "traditionalUnits": balances["traditional_units"],
                "tokenizedUnits": balances["tokenized_units"],
            }
            cur.execute(sql["store_result"], {**params, "result": Jsonb(result)})
            expect_rowcount(cur, 1, "store_result")
        # The block ended without an exception: psycopg has sent COMMIT.
        return Outcome(201, result)
    except RollbackAndRespond as stop:
        return stop.outcome

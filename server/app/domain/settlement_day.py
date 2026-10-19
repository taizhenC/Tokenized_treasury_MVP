"""The settlement day (Lesson 13): the operator's two end-of-day steps.

valued_cutoff     every deliver order still RECYCLING is DROPPED, phase OPEN -> VALUED_CUTOFF_PASSED
                  (DTC: valued items still pending at about 3:10pm ET drop)   valued_cutoff.sql
final_settlement  final figures per account, the NSS file (one net-net figure per settling bank),
                  closing cash lines, every net back to 0, the next weekday opens
                  (DTC: final figures about 3:45pm ET, NSS file with the Fed about 4:30pm ET)
                                                                              final_settlement.sql

Both lock the settlement_day row (the sequencer) first, like every flow that can settle a delivery,
so a step never runs in the middle of a deliver order or a settlement progress payment.
"""

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import queries
from app.domain.idempotency import request_hash, reserve
from app.domain.outcome import Outcome, RollbackAndRespond, reject
from app.domain.transactions import expect_rowcount, transaction

CUTOFF_SQL = queries.load("valued_cutoff", queries.VALUED_CUTOFF_BLOCKS)
FINAL_SQL = queries.load("final_settlement", queries.FINAL_SETTLEMENT_BLOCKS)


def valued_cutoff(conn: Connection, *, actor_user_id: int, idempotency_key: str) -> Outcome:
    """Drop today's still-recycling orders and close valued processing, in one transaction on `conn`.

    The caller has already checked the role (OPERATOR). The request has no input but the key: the
    day comes from the locked settlement_day row, never from the body.
    """
    sql = CUTOFF_SQL
    params: dict[str, Any] = {
        "actor_user_id": actor_user_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash("VALUED_CUTOFF"),
    }
    try:
        with transaction(conn):
            cur = conn.cursor(row_factory=dict_row)
            params["operation_id"] = reserve(cur, sql, params)

            # The sequencer, locked THEN read: a cutoff that waited behind another one sees its phase.
            day = cur.execute(sql["lock_settlement_day"]).fetchone()
            if day is None:
                raise RuntimeError("no settlement_day row: run the seeds first (npm run db:seed)")
            if day["phase"] != "OPEN":
                raise reject(422, "the valued cutoff has already run today")
            params["business_date"] = day["business_date"]

            # RETURNING has no order: sort the ids so the result (and a replay) is stable.
            dropped_rows = cur.execute(sql["drop_recycling_instructions"], params).fetchall()
            dropped = sorted(row["id"] for row in dropped_rows)
            cur.execute(sql["close_valued_processing"], params)
            expect_rowcount(cur, 1, "close_valued_processing")

            result = {
                "operationId": params["operation_id"],
                "type": "VALUED_CUTOFF",
                "businessDate": day["business_date"].isoformat(),  # JSON-native: a string, not a date
                "droppedInstructionIds": dropped,
            }
            cur.execute(sql["store_result"], {**params, "result": Jsonb(result)})
            expect_rowcount(cur, 1, "store_result")
        return Outcome(201, result)
    except RollbackAndRespond as stop:
        return stop.outcome


def final_settlement(conn: Connection, *, actor_user_id: int, idempotency_key: str) -> Outcome:
    """Close the day's money and open the next business day, in one transaction on `conn`.

    The caller has already checked the role (OPERATOR). Everything below commits together: a crash
    halfway leaves the day exactly as it was (still VALUED_CUTOFF_PASSED), so the step can run again.
    """
    sql = FINAL_SQL
    params: dict[str, Any] = {
        "actor_user_id": actor_user_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash("FINAL_SETTLEMENT"),
    }
    try:
        with transaction(conn):
            cur = conn.cursor(row_factory=dict_row)
            params["operation_id"] = reserve(cur, sql, params)

            # Lock order: the sequencer, then every settlement account (lowest account_id first).
            day = cur.execute(sql["lock_settlement_day"]).fetchone()
            if day is None:
                raise RuntimeError("no settlement_day row: run the seeds first (npm run db:seed)")
            if day["phase"] != "VALUED_CUTOFF_PASSED":
                raise reject(422, "run the valued cutoff first")
            params["business_date"] = day["business_date"]
            accounts = cur.execute(sql["lock_settlement_accounts"]).fetchall()

            # The final figures: every locked account's net, before it is set to 0.
            figures = cur.execute(sql["record_settlement_figures"], params).fetchall()
            if len(figures) != len(accounts):
                raise RuntimeError(f"recorded {len(figures)} settlement figures for {len(accounts)} accounts")
            # One net-net line per settling bank, already ordered by bank id. An account without a
            # bank is left out on purpose: reconciliation then reports the gap (see the SQL).
            nss_file = cur.execute(sql["record_nss_file"], params).fetchall()

            # Closing cash lines: only nonzero amounts get a line (CHECK amount_cents <> 0).
            nonzero_accounts = sum(1 for row in figures if row["final_net_cents"] != 0)
            cur.execute(sql["post_closing_participant_entries"], params)
            expect_rowcount(cur, nonzero_accounts, "post_closing_participant_entries")
            cur.execute(sql["post_closing_fed_entries"], params)
            expect_rowcount(
                cur, sum(1 for line in nss_file if line["net_net_cents"] != 0), "post_closing_fed_entries"
            )

            cur.execute(sql["zero_settlement_accounts"], params)
            expect_rowcount(cur, nonzero_accounts, "zero_settlement_accounts")
            cur.execute(sql["open_next_business_day"], params)
            expect_rowcount(cur, 1, "open_next_business_day")
            next_business_date = cur.fetchone()["business_date"]

            result = {
                "operationId": params["operation_id"],
                "type": "FINAL_SETTLEMENT",
                # JSON-native: dates as ISO strings, cents as ints (BIGINT columns, never a Decimal).
                "businessDate": day["business_date"].isoformat(),
                "nextBusinessDate": next_business_date.isoformat(),
                "figures": [
                    {"accountId": row["account_id"], "finalNetCents": row["final_net_cents"]}
                    for row in sorted(figures, key=lambda row: row["account_id"])
                ],
                "nssFile": [
                    {
                        "settlingBankId": line["settling_bank_id"],
                        "bankName": line["bank_name"],
                        "netNetCents": line["net_net_cents"],
                    }
                    for line in nss_file
                ],
            }
            cur.execute(sql["store_result"], {**params, "result": Jsonb(result)})
            expect_rowcount(cur, 1, "store_result")
        return Outcome(201, result)
    except RollbackAndRespond as stop:
        return stop.outcome

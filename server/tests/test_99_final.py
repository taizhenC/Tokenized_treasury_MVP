"""After everything above (hundreds of concurrent operations, several settlement days): the books
still balance - units and cash alike."""

from tests.test_90_settlement import SETTLEMENT_CHECKS, sql_one, sql_rows


def test_final_reconciliation_passes(users, probe):
    result = users["auditor"].get("/api/reconciliation").json()
    assert result["passed"] is True, result
    assert result["conservation"][0]["held_total"] == 3000
    assert probe.value("held_total") == 3000


def test_every_operation_except_opening_still_nets_to_zero(probe):
    assert all(r["net"] == 0 for r in probe.rows("ledger_net_per_operation") if r["operation_type"] != "OPENING")


def test_every_operations_cash_lines_net_to_zero(probe):
    nets = sql_rows(probe, "cash_net_per_operation")
    assert nets, "the settlement modules wrote cash lines"
    assert [r for r in nets if r["net"] != 0] == []


def test_every_settlement_check_ran_and_passed(users):
    result = users["auditor"].get("/api/reconciliation").json()
    for key in SETTLEMENT_CHECKS:
        assert result[key], f"{key} returned no rows"
        assert all(row["ok"] is True for row in result[key]), (key, result[key])


def test_the_suite_leaves_the_settlement_day_open_and_flat(probe):
    status = sql_one(probe, "day_status")
    assert (status["phase"], status["accounts_not_flat"]) == ("OPEN", 0), status
    assert sql_one(probe, "recycling_on_closed_days")["n"] == 0  # every cutoff dropped what was still waiting

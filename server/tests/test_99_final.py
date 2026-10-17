"""After everything above (hundreds of concurrent operations): the books still balance."""


def test_final_reconciliation_passes(users, probe):
    result = users["auditor"].get("/api/reconciliation").json()
    assert result["passed"] is True, result
    assert result["conservation"][0]["held_total"] == 3000
    assert probe.value("held_total") == 3000


def test_every_operation_except_opening_still_nets_to_zero(probe):
    assert all(r["net"] == 0 for r in probe.rows("ledger_net_per_operation") if r["operation_type"] != "OPENING")


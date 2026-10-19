"""Lesson 11: the two DTC-style risk controls as pure arithmetic (app/domain/risk.py) - no database.

Collateral Monitor (CM) = collateral value + net settlement balance, must stay >= 0.
Net Debit Cap (NDC):      net settlement balance >= -cap.
Every number of the demo scenario (README, SPEC section 1) is replayed here through risk.py, step
by step, so a wrong formula fails on the exact figure the lesson teaches. Collateral counts
traditional units only: tokenized units have no collateral value (the 11 Dec 2025 no-action letter).
"""

from dataclasses import replace

import pytest

from app.domain import risk
from app.domain.risk import AccountState

CPU = 9500  # collateral cents per unit: $100.00 prior close less a 5% haircut
MINT_MESSAGE = "collateral monitor would go negative: tokenized units have no collateral value"

# The fresh seed: 1,000 traditional units each (Delta none), net 0, collateral 1,000 x 9,500.
ALPHA = AccountState(account_id=101, traditional_units=1000, net_cents=0,
                     collateral_value_cents=9_500_000, net_debit_cap_cents=3_844_000)
BETA = AccountState(account_id=102, traditional_units=1000, net_cents=0,
                    collateral_value_cents=9_500_000, net_debit_cap_cents=1_500_000)
GAMMA = AccountState(account_id=103, traditional_units=1000, net_cents=0,
                     collateral_value_cents=9_500_000, net_debit_cap_cents=2_500_000)
DELTA = AccountState(account_id=104, traditional_units=0, net_cents=0,
                     collateral_value_cents=0, net_debit_cap_cents=0)


def evaluate(deliverer, receiver, quantity, amount_cents):
    return risk.evaluate_delivery(deliverer=deliverer, receiver=receiver, quantity=quantity,
                                  amount_cents=amount_cents, collateral_cents_per_unit=CPU)


def deliver(deliverer, receiver, quantity, amount_cents):
    return risk.after_delivery(deliverer=deliverer, receiver=receiver, quantity=quantity,
                               amount_cents=amount_cents, collateral_cents_per_unit=CPU)


def minted(state, quantity):
    """A mint the app allowed: the units leave traditional form, and with them their collateral."""
    return replace(state, traditional_units=state.traditional_units - quantity,
                   collateral_value_cents=state.collateral_value_cents - quantity * CPU)


def paid(state, amount_cents):
    """A settlement progress payment: Fed funds in, the net balance goes up."""
    return replace(state, net_cents=state.net_cents + amount_cents)


# ---------- the reference data ----------

def test_collateral_per_unit_is_the_prior_close_less_the_haircut():
    assert risk.collateral_cents_per_unit(10_000, 500) == CPU  # $100.00 less 5% = $95.00
    assert risk.collateral_cents_per_unit(10_000, 200) == 9_800  # DTC's smallest haircut, 2%
    assert risk.collateral_cents_per_unit(10_000, 10_000) == 0  # a 100% haircut: no collateral value
    assert risk.collateral_cents_per_unit(9_999, 500) == 9_499  # 9,499.05 rounds DOWN: never overstated


@pytest.mark.parametrize("avg,factor,cap", [
    (3_200_000, 12_500, 3_844_000),  # alpha: $32,000 x 1.25 = $40,000 -> capped at the maximum
    (1_000_000, 15_000, 1_500_000),  # beta:  $10,000 x 1.50
    (2_000_000, 12_500, 2_500_000),  # gamma: $20,000 x 1.25
    (0, 10_000, 0),                  # Delta: $0, a lab choice (DTC would apply its minimum cap)
    (2_000_000, 20_000, 3_844_000),  # a factor of 2 still stops at the maximum
    (1, 15_000, 1),                  # 1.5 cents rounds down
])
def test_net_debit_cap_is_the_peak_average_times_the_factor_capped_at_the_maximum(avg, factor, cap):
    assert risk.net_debit_cap(avg, factor, 3_844_000) == cap


def test_seeded_states_monitor_and_headroom():
    assert [risk.collateral_monitor(s) for s in (ALPHA, BETA, GAMMA, DELTA)] == [9_500_000] * 3 + [0]
    # Headroom = the largest debit an account can take now: the smaller of its CM and its cap room.
    assert [risk.headroom(s) for s in (ALPHA, BETA, GAMMA, DELTA)] == [3_844_000, 1_500_000, 2_500_000, 0]


# ---------- the demo scenario, step by step ----------

def test_step1_alpha_to_beta_recycles_on_betas_net_debit_cap():
    # beta would be at -2,000,000; its cap is 1,500,000. Its CM would be fine: 11.4M - 2M.
    assert evaluate(ALPHA, BETA, 200, 2_000_000) == risk.RECEIVER_NET_DEBIT_CAP


def test_step2_beta_to_gamma_settles_and_then_releases_order_1():
    assert evaluate(BETA, GAMMA, 100, 1_000_000) is None
    beta, gamma = deliver(BETA, GAMMA, 100, 1_000_000)
    assert beta == AccountState(102, 900, 1_000_000, 8_550_000, 1_500_000)
    assert gamma == AccountState(103, 1100, -1_000_000, 10_450_000, 2_500_000)
    # The retry: beta now has a +1,000,000 credit, so its net after order #1 is -1,000,000 >= -1,500,000.
    assert evaluate(ALPHA, beta, 200, 2_000_000) is None
    alpha, beta = deliver(ALPHA, beta, 200, 2_000_000)
    assert (alpha.traditional_units, alpha.net_cents) == (800, 2_000_000)
    assert (beta.traditional_units, beta.net_cents) == (1100, -1_000_000)
    assert (gamma.traditional_units, gamma.net_cents) == (1100, -1_000_000)
    assert risk.headroom(beta) == 500_000  # 1,500,000 cap less its 1,000,000 debit


def scenario_after_step2():
    beta, gamma = deliver(BETA, GAMMA, 100, 1_000_000)
    alpha, beta = deliver(ALPHA, beta, 200, 2_000_000)
    return alpha, beta, gamma


def test_step3_gamma_mints_995_refused_994_allowed():
    _, _, gamma = scenario_after_step2()
    # 995: 105 units of collateral left -> 105 x 9,500 - 1,000,000 = -2,500.
    assert risk.mint_problem(gamma, 995, CPU) == MINT_MESSAGE
    # 994: 106 x 9,500 - 1,000,000 = 7,000.
    assert risk.mint_problem(gamma, 994, CPU) is None
    gamma = minted(gamma, 994)
    assert (gamma.traditional_units, gamma.collateral_value_cents) == (106, 1_007_000)
    assert risk.collateral_monitor(gamma) == 7_000
    assert risk.headroom(gamma) == 7_000  # the CM, not the cap room (1,500,000), is the limit now


def scenario_after_step3():
    alpha, beta, gamma = scenario_after_step2()
    return alpha, beta, minted(gamma, 994)


def test_step4_two_more_orders_recycle_for_different_reasons():
    alpha, beta, gamma = scenario_after_step3()
    # #3 to gamma: (106 + 100) x 9,500 - 2,000,000 = -43,000.
    assert evaluate(alpha, gamma, 100, 1_000_000) == risk.RECEIVER_COLLATERAL_MONITOR
    assert (gamma.collateral_value_cents + 100 * CPU) + (gamma.net_cents - 1_000_000) == -43_000
    # #4 to beta: -1,000,000 - 1,000,000 = -2,000,000 < -1,500,000.
    assert evaluate(alpha, beta, 100, 1_000_000) == risk.RECEIVER_NET_DEBIT_CAP


def test_step5_gammas_payment_releases_order_3_but_not_order_4():
    alpha, beta, gamma = scenario_after_step3()
    gamma = paid(gamma, 50_000)
    assert gamma.net_cents == -950_000
    # Retries run largest amount first, then by id: #3 before #4 (both 1,000,000).
    assert evaluate(alpha, gamma, 100, 1_000_000) is None
    alpha, gamma = deliver(alpha, gamma, 100, 1_000_000)
    assert risk.collateral_monitor(gamma) == 7_000  # 206 x 9,500 - 1,950,000
    assert evaluate(alpha, beta, 100, 1_000_000) == risk.RECEIVER_NET_DEBIT_CAP  # #4 still waits


def test_step6_and_7_final_figures_nss_file_and_units():
    alpha, beta, gamma = scenario_after_step3()
    alpha, gamma = deliver(alpha, paid(gamma, 50_000), 100, 1_000_000)
    figures = {s.account_id: s.net_cents for s in (alpha, beta, gamma, DELTA)}
    assert figures == {101: 3_000_000, 102: -1_000_000, 103: -1_950_000, 104: 0}
    bank_one, bank_two = figures[101] + figures[102], figures[103] + figures[104]
    assert (bank_one, bank_two) == (2_000_000, -1_950_000)
    # Deliveries only move money between participants; the whole file nets to the one payment.
    assert bank_one + bank_two == 50_000
    assert [s.traditional_units for s in (alpha, beta, gamma)] == [700, 1100, 206]
    assert sum(s.traditional_units for s in (alpha, beta, gamma, DELTA)) + 994 == 3000
    # Collateral always equals traditional units x 9,500: the 994 tokenized units count zero.
    assert all(s.collateral_value_cents == s.traditional_units * CPU for s in (alpha, beta, gamma, DELTA))


# ---------- the checks, one at a time, and their order ----------

def test_insufficient_position_comes_first():
    assert evaluate(DELTA, ALPHA, 1, 1) == risk.INSUFFICIENT_POSITION
    assert evaluate(ALPHA, BETA, 1001, 1) == risk.INSUFFICIENT_POSITION
    assert evaluate(ALPHA, BETA, 1000, 1) is None  # exactly all of it is enough
    # Even when the receiver would also break both of its controls, the position is reported.
    assert evaluate(DELTA, BETA, 1, 10_000_000_000_000) == risk.INSUFFICIENT_POSITION


def test_deliverer_collateral_monitor():
    """Delivering BELOW collateral value while in debit: the units (and their collateral) leave,
    and the price received does not make up for it."""
    _, _, gamma = scenario_after_step3()  # 106 units, net -1,000,000, CM 7,000
    assert evaluate(gamma, ALPHA, 100, 1) == risk.DELIVERER_COLLATERAL_MONITOR
    # At 943,000 the deliverer's CM is exactly 0: (1,007,000 - 950,000) + (-1,000,000 + 943,000).
    assert evaluate(gamma, ALPHA, 100, 943_000) is None
    assert evaluate(gamma, ALPHA, 100, 942_999) == risk.DELIVERER_COLLATERAL_MONITOR
    # Both sides fail here: gamma's CM (7,000 - 950,000 + 1 < 0) and Delta's $0 cap (-1 < -0). The
    # deliverer's reason wins because it is check 2; the receiver's cap is only check 4.
    assert evaluate(gamma, DELTA, 100, 1) == risk.DELIVERER_COLLATERAL_MONITOR


def test_receiver_collateral_monitor_boundary_zero_passes():
    alpha, _, gamma = scenario_after_step3()  # gamma: 106 units, net -1,000,000
    # (1,007,000 + 950,000) + (-1,000,000 - 957,000) = 0: a CM of exactly zero is allowed.
    assert evaluate(alpha, gamma, 100, 957_000) is None
    assert evaluate(alpha, gamma, 100, 957_001) == risk.RECEIVER_COLLATERAL_MONITOR


def test_receiver_collateral_monitor_is_reported_before_the_net_debit_cap():
    alpha, _, gamma = scenario_after_step3()
    # 2,000,000: CM 1,957,000 - 3,000,000 < 0 AND net -3,000,000 < -2,500,000 -> the CM is named.
    assert evaluate(alpha, gamma, 100, 2_000_000) == risk.RECEIVER_COLLATERAL_MONITOR


def test_receiver_net_debit_cap_boundary_exactly_the_cap_passes():
    assert evaluate(ALPHA, BETA, 1, 1_500_000) is None  # net -1,500,000 = -cap
    assert evaluate(ALPHA, BETA, 1, 1_500_001) == risk.RECEIVER_NET_DEBIT_CAP


def test_delta_cannot_take_even_one_cent_of_debit():
    """A lab choice: Delta's cap is $0, so it can never be debited (DTC would apply its established
    minimum net debit cap; this lab has none). Collateral alone is not enough."""
    assert evaluate(ALPHA, DELTA, 1, 1) == risk.RECEIVER_NET_DEBIT_CAP


def test_after_delivery_moves_units_money_and_collateral_and_nothing_else():
    alpha, beta = deliver(ALPHA, BETA, 10, 123_456)
    assert alpha == AccountState(101, 990, 123_456, 9_500_000 - 95_000, 3_844_000)
    assert beta == AccountState(102, 1010, -123_456, 9_500_000 + 95_000, 1_500_000)
    assert ALPHA.traditional_units == 1000  # the inputs are not changed (frozen states)


def test_mint_boundary_zero_passes():
    state = AccountState(103, 1100, -950_000, 10_450_000, 2_500_000)
    assert risk.mint_problem(state, 1000, CPU) is None  # (10,450,000 - 9,500,000) - 950,000 = 0
    assert risk.mint_problem(replace(state, net_cents=-950_001), 1000, CPU) == MINT_MESSAGE
    assert risk.mint_problem(GAMMA, 1000, CPU) is None  # no debit: minting everything is fine


def test_headroom_is_never_negative():
    breached = AccountState(103, 0, -10, 0, 0)  # a state the CHECKs would never let exist
    assert risk.collateral_monitor(breached) == -10
    assert risk.headroom(breached) == 0
    credit = AccountState(101, 800, 2_000_000, 7_600_000, 3_844_000)
    assert risk.headroom(credit) == 5_844_000  # a credit adds room under the cap: 3,844,000 + 2,000,000


# ---------- what each party is told ----------

DELIVERER_SIDE = [risk.INSUFFICIENT_POSITION, risk.DELIVERER_COLLATERAL_MONITOR]
RECEIVER_SIDE = [risk.RECEIVER_COLLATERAL_MONITOR, risk.RECEIVER_NET_DEBIT_CAP]


def shown(reason, viewer):
    return risk.reason_for_viewer(reason, viewer_account_id=viewer,
                                  deliverer_account_id=101, receiver_account_id=102)


@pytest.mark.parametrize("reason", DELIVERER_SIDE + RECEIVER_SIDE)
def test_auditor_and_operator_see_the_exact_reason(reason):
    assert shown(reason, None) == reason


@pytest.mark.parametrize("reason", DELIVERER_SIDE)
def test_a_deliverer_side_reason_is_masked_for_everyone_but_the_deliverer(reason):
    assert shown(reason, 101) == reason
    assert shown(reason, 102) == risk.WAITING_ON_COUNTERPARTY
    assert shown(reason, 103) == risk.WAITING_ON_COUNTERPARTY


@pytest.mark.parametrize("reason", RECEIVER_SIDE)
def test_a_receiver_side_reason_is_masked_for_everyone_but_the_receiver(reason):
    """A lab choice: alpha learns that beta is the hold-up, not that beta is near its cap."""
    assert shown(reason, 102) == reason
    assert shown(reason, 101) == risk.WAITING_ON_COUNTERPARTY
    assert shown(reason, 103) == risk.WAITING_ON_COUNTERPARTY


@pytest.mark.parametrize("viewer", [None, 101, 102, 103])
def test_no_reason_stays_no_reason(viewer):
    assert shown(None, viewer) is None


def test_reason_names():
    assert (risk.INSUFFICIENT_POSITION, risk.DELIVERER_COLLATERAL_MONITOR, risk.RECEIVER_COLLATERAL_MONITOR,
            risk.RECEIVER_NET_DEBIT_CAP, risk.WAITING_ON_COUNTERPARTY) == (
        "INSUFFICIENT_POSITION", "DELIVERER_COLLATERAL_MONITOR", "RECEIVER_COLLATERAL_MONITOR",
        "RECEIVER_NET_DEBIT_CAP", "WAITING_ON_COUNTERPARTY")

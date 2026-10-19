"""Settlement risk controls (Lesson 12): pure arithmetic - no SQL, no I/O, no imports from the app.

Why controls at all: in DVP settlement the securities move one delivery at a time, in real time, but
the money is netted: each participant ends the day with one net figure, and each settling bank pays
or is paid one net-net amount at the end of the day (apart from settlement progress payments, Fed
funds a participant may wire in during the day). Between the two, a participant may have received
securities it has not paid for yet. If it then fails to pay, settlement must still complete, so
every delivery must first pass two controls:

    Collateral Monitor (CM) = collateral value + net settlement balance      must stay >= 0
                              (its debit is always covered by collateral after haircuts)
    Net Debit Cap (NDC):      net settlement balance >= -cap                  a debit never exceeds the cap
                              (DTC sizes its liquidity so settlement completes even if the largest
                              participant or affiliated family fails)

(DTC has a third primary control, the IPN collateral control, that this lab does not model.)
A delivery that would break a control does not fail: it RECYCLES (waits); in this lab it is retried
when a credit arrives. Every number below is in whole CENTS, and every example is from the demo seed:
price $100.00, haircut 5% -> collateral 9,500 cents per unit; 1,000 units -> $95,000 of collateral;
caps alpha 3,844,000 (the maximum), beta 1,500,000, gamma 2,500,000, Delta 0.

The database has the same two rules as CHECKs on settlement_accounts (the guarantee); this module
is the app's check that comes first, so a delivery that would break a rule recycles with a clear
reason instead of failing with a constraint error.
"""

from dataclasses import dataclass

# Why an order is still waiting (the four values the delivery_instructions CHECK allows).
INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"  # the deliverer lacks the units
DELIVERER_COLLATERAL_MONITOR = "DELIVERER_COLLATERAL_MONITOR"  # the deliverer's CM would go negative
RECEIVER_COLLATERAL_MONITOR = "RECEIVER_COLLATERAL_MONITOR"  # the receiver's CM would go negative
RECEIVER_NET_DEBIT_CAP = "RECEIVER_NET_DEBIT_CAP"  # the receiver's debit would exceed its cap
# What a party sees for the OTHER side's reason (a lab choice: one participant's collateral and
# cap are private, so the counterparty only learns that the order waits on the other side).
WAITING_ON_COUNTERPARTY = "WAITING_ON_COUNTERPARTY"

DELIVERER_SIDE = frozenset({INSUFFICIENT_POSITION, DELIVERER_COLLATERAL_MONITOR})
RECEIVER_SIDE = frozenset({RECEIVER_COLLATERAL_MONITOR, RECEIVER_NET_DEBIT_CAP})

MINT_BREACHES_CM = "collateral monitor would go negative: tokenized units have no collateral value"


@dataclass(frozen=True)
class AccountState:
    """One participant's settlement position at one moment (all money in cents).

    net_cents > 0 is a credit (DTC owes the participant: it delivered and waits to be paid);
    net_cents < 0 is a debit (the participant owes DTC: it received and pays at the end of the day).
    """

    account_id: int
    traditional_units: int
    net_cents: int
    collateral_value_cents: int
    net_debit_cap_cents: int


def collateral_cents_per_unit(prior_close_cents: int, haircut_bps: int) -> int:
    """What one unit is worth AS COLLATERAL: yesterday's close less the haircut (1 bp = 0.01%).

    Demo: 10,000 cents x (10,000 - 500) // 10,000 = 9,500 cents ($95.00). Integer division rounds
    down, so collateral is never overstated - the same arithmetic as the generated column
    collateral_prices.collateral_cents_per_unit.
    """
    return prior_close_cents * (10000 - haircut_bps) // 10000


def net_debit_cap(avg_top3_peak_cents: int, factor_bps: int, max_cents: int) -> int:
    """The largest debit a participant may run: its usual peak debit times a factor, capped.

    DTC: the average of the 3 highest intraday net-debit peaks over a rolling 70 business days,
    times a factor between 1 and 2 (10,000..20,000 bps), never above DTC's maximum NDC.
    Demo: alpha 3,200,000 x 1.25 = 4,000,000 -> capped at the maximum 3,844,000;
    beta 1,000,000 x 1.5 = 1,500,000; gamma 2,000,000 x 1.25 = 2,500,000; Delta 0 x 1 = 0.
    """
    return min(max_cents, avg_top3_peak_cents * factor_bps // 10000)


def collateral_monitor(state: AccountState) -> int:
    """CM = collateral value + net settlement balance. It must never go negative.

    A debit uses up collateral; a credit adds to it. Demo: a fresh account has CM 9,500,000 + 0.
    """
    return state.collateral_value_cents + state.net_cents


def headroom(state: AccountState) -> int:
    """The largest NEW debit the account could take right now without breaking either control.

    Both limits apply, so it is the smaller one: the CM left over, and the cap left over
    (cap + net). Never below 0. Demo: beta after step 2 (net -1,000,000, cap 1,500,000) has
    500,000 cap headroom left, much less than its CM; Delta (cap 0) has none at all.
    (A receiver's collateral also grows by the units it receives; this number leaves that out.)
    """
    return max(0, min(collateral_monitor(state), state.net_debit_cap_cents + state.net_cents))


def evaluate_delivery(
    *,
    deliverer: AccountState,
    receiver: AccountState,
    quantity: int,
    amount_cents: int,
    collateral_cents_per_unit: int,
) -> str | None:
    """Would this delivery break a control? None = it passes and may settle now.

    The checks run IN THIS ORDER and the first failure is the recycle reason:
      1 the deliverer has the units                          else INSUFFICIENT_POSITION
      2 the deliverer's CM after the delivery >= 0           else DELIVERER_COLLATERAL_MONITOR
        (it loses the units' collateral value, gains the amount as a credit)
      3 the receiver's CM after the delivery >= 0            else RECEIVER_COLLATERAL_MONITOR
        (it gains the units' collateral value, takes the amount as a debit)
      4 the receiver's net after the delivery >= -its cap    else RECEIVER_NET_DEBIT_CAP

    Demo step 1: alpha delivers 200 units to beta for 2,000,000. Beta's CM would be
    (9,500,000 + 200 x 9,500) + (0 - 2,000,000) = 9,400,000 (fine), but its net -2,000,000 is
    below -1,500,000 -> RECEIVER_NET_DEBIT_CAP. The order waits.
    Demo step 4: alpha delivers 100 to gamma for 1,000,000. Gamma (106 traditional units, net
    -1,000,000) would have CM (106 x 9,500 + 100 x 9,500) + (-1,000,000 - 1,000,000) = -43,000
    -> RECEIVER_COLLATERAL_MONITOR.
    A CM of exactly 0 passes: the collateral then covers the debit exactly.
    """
    collateral_moved = quantity * collateral_cents_per_unit
    if deliverer.traditional_units < quantity:
        return INSUFFICIENT_POSITION
    if (deliverer.collateral_value_cents - collateral_moved) + (deliverer.net_cents + amount_cents) < 0:
        return DELIVERER_COLLATERAL_MONITOR
    if (receiver.collateral_value_cents + collateral_moved) + (receiver.net_cents - amount_cents) < 0:
        return RECEIVER_COLLATERAL_MONITOR
    if receiver.net_cents - amount_cents < -receiver.net_debit_cap_cents:
        return RECEIVER_NET_DEBIT_CAP
    return None


def after_delivery(
    *,
    deliverer: AccountState,
    receiver: AccountState,
    quantity: int,
    amount_cents: int,
    collateral_cents_per_unit: int,
) -> tuple[AccountState, AccountState]:
    """The two new states after the delivery settles (pure arithmetic, no check).

    The deliverer gives the units (and their collateral value) and gets a credit of the amount;
    the receiver gets the units (and their collateral value) and a debit of the amount.
    Demo step 2: beta delivers 100 to gamma for 1,000,000 -> beta 900 units, net +1,000,000,
    collateral 8,550,000; gamma 1,100 units, net -1,000,000, collateral 10,450,000.
    """
    collateral_moved = quantity * collateral_cents_per_unit
    new_deliverer = AccountState(
        account_id=deliverer.account_id,
        traditional_units=deliverer.traditional_units - quantity,
        net_cents=deliverer.net_cents + amount_cents,
        collateral_value_cents=deliverer.collateral_value_cents - collateral_moved,
        net_debit_cap_cents=deliverer.net_debit_cap_cents,
    )
    new_receiver = AccountState(
        account_id=receiver.account_id,
        traditional_units=receiver.traditional_units + quantity,
        net_cents=receiver.net_cents - amount_cents,
        collateral_value_cents=receiver.collateral_value_cents + collateral_moved,
        net_debit_cap_cents=receiver.net_debit_cap_cents,
    )
    return new_deliverer, new_receiver


def mint_problem(state: AccountState, quantity: int, collateral_cents_per_unit: int) -> str | None:
    """None if converting `quantity` traditional units to tokenized keeps the CM >= 0, else the 422 message.

    Tokenized units get NO collateral value (SEC staff no-action letter of 11 Dec 2025), so a mint
    takes their collateral value away. The caller has already checked traditional_units >= quantity.
    Demo step 3: gamma (1,100 units, net -1,000,000) mints 995: CM (105 x 9,500) - 1,000,000 =
    -2,500 -> refused. Minting 994 leaves (106 x 9,500) - 1,000,000 = 7,000 -> allowed.
    (A lab choice: a breaching mint is refused at once with 422, it does not recycle.)
    """
    if (state.collateral_value_cents - quantity * collateral_cents_per_unit) + state.net_cents >= 0:
        return None
    return MINT_BREACHES_CM


def reason_for_viewer(
    reason: str | None,
    *,
    viewer_account_id: int | None,
    deliverer_account_id: int,
    receiver_account_id: int,
) -> str | None:
    """The recycle reason as one viewer may see it (a lab choice: the other side's reason is masked).

    viewer None (auditor or operator) or no reason -> the reason unchanged. A deliverer-side reason
    seen by anyone but the deliverer, or a receiver-side reason seen by anyone but the receiver,
    becomes WAITING_ON_COUNTERPARTY: the reason would reveal the other participant's collateral or cap.
    Demo step 1: beta sees RECEIVER_NET_DEBIT_CAP (its own cap); alpha sees WAITING_ON_COUNTERPARTY.
    """
    if viewer_account_id is None or reason is None:
        return reason
    if reason in DELIVERER_SIDE and viewer_account_id != deliverer_account_id:
        return WAITING_ON_COUNTERPARTY
    if reason in RECEIVER_SIDE and viewer_account_id != receiver_account_id:
        return WAITING_ON_COUNTERPARTY
    return reason

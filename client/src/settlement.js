// Display math for the settlement screens. The server's engine decides everything
// (server/app/domain/risk.py); these helpers only recompute, from the balances already on screen,
// the numbers that explain a decision.
import { formatCents, formatSignedCents, formatUnits, shortName } from './format.js'

// The lab's maximum net debit cap is DTC's real one, scaled down like every dollar figure in the lab.
export const LAB_MAXIMUM_NOTE =
  "lab figure: DTC's $3.844B maximum (since 6 Apr 2026) scaled down 1:100,000, like every dollar figure here"

// Reasons about the deliverer's side; every other reason is about the receiver's side.
const DELIVERER_SIDE = new Set(['INSUFFICIENT_POSITION', 'DELIVERER_COLLATERAL_MONITOR'])

// How much of its net debit cap an account is using: debit = the part of net below zero.
export function capUse(netCents, capCents) {
  const debit = Math.max(0, -netCents)
  let percent
  if (capCents > 0) percent = Math.min(100, (debit / capCents) * 100)
  else percent = debit > 0 ? 100 : 0
  return { debit, percent }
}

// The cap's inputs, as DTC computes it: average of the 3 highest intraday net-debit peaks x a
// factor (1 to 2), never above DTC's maximum. BIGINT division in the database rounds down; so does this.
export function capInputs(row, maxCents) {
  const raw = Math.floor((row.avg_top3_peak_cents * row.factor_bps) / 10000)
  return { raw, capped: maxCents !== undefined && maxCents !== null && raw > maxCents }
}

// How to name an account in the explanations: "Your" for the signed-in participant's own account.
function speaker(account, accountId, viewerAccountId) {
  const you = accountId === viewerAccountId
  const name = account.participant_name ? shortName(account) : `account ${accountId}`
  return { you, name, whose: you ? 'Your' : `${name}'s`, its: you ? 'your' : 'its' }
}

// Checks 1 and 2 of risk.evaluate_delivery (the deliverer's side), with the same arithmetic.
function delivererChecks(row, account, cpu, viewerAccountId) {
  const { you, name, whose } = speaker(account, row.deliverer_account_id, viewerAccountId)
  const q = row.quantity
  // The units leave (their collateral value goes with them); the payment arrives as a credit.
  const collateral = account.collateral_value_cents - q * cpu
  const net = account.net_cents + row.amount_cents
  return [
    {
      reason: 'INSUFFICIENT_POSITION',
      passes: account.traditional_units >= q,
      text:
        `${you ? 'You hold' : `${name} holds`} ${formatUnits(account.traditional_units)} traditional units; ` +
        `the order needs ${formatUnits(q)}.`,
    },
    {
      reason: 'DELIVERER_COLLATERAL_MONITOR',
      passes: collateral + net >= 0,
      text:
        `${whose} collateral monitor would be ${formatCents(collateral + net)}: collateral ` +
        `${formatCents(collateral)} after the units leave, net ${formatSignedCents(net)}.`,
    },
  ]
}

// Checks 3 and 4 of risk.evaluate_delivery (the receiver's side), with the same arithmetic.
function receiverChecks(row, account, cpu, viewerAccountId) {
  const { whose, its } = speaker(account, row.receiver_account_id, viewerAccountId)
  // The units arrive (with their collateral value); the payment is a new debit.
  const collateral = account.collateral_value_cents + row.quantity * cpu
  const net = account.net_cents - row.amount_cents
  return [
    {
      reason: 'RECEIVER_COLLATERAL_MONITOR',
      passes: collateral + net >= 0,
      text:
        `${whose} collateral monitor would be ${formatCents(collateral + net)}: collateral ` +
        `${formatCents(collateral)} with the units, net ${formatSignedCents(net)}.`,
    },
    {
      reason: 'RECEIVER_NET_DEBIT_CAP',
      passes: net >= -account.net_debit_cap_cents,
      text:
        `${whose} net would be ${formatSignedCents(net)}, beyond ${its} ` +
        `${formatCents(account.net_debit_cap_cents)} net debit cap.`,
    },
  ]
}

// The numbers behind one waiting order's recycle reason. Every check risk.evaluate_delivery makes is
// recomputed for each side whose account is on screen, in the engine's order (1-2 deliverer, 3-4
// receiver), because the stored reason names only the FIRST check that failed at the last try.
// Returns null when the reason is masked or the account it is about is not on screen (a participant
// sees only its own account); otherwise one of:
//   { verdict: 'fails', reason, text, changed }  the first check that fails now; changed when it is
//                                                not the stored reason (the next retry records it)
//   { verdict: 'passes' }                        both sides on screen and all four checks pass
//   { verdict: 'side-passes' }                   only one side on screen, and its checks pass
//   row:            a delivery instruction (snake_case, as GET /api/delivery-orders returns it)
//   accountsById:   Map of settlement-account rows (GET /api/settlement/accounts)
//   cpu:            collateral cents per unit (GET /api/settlement/day)
//   viewerAccountId the signed-in participant's account, so its own figures read "your"
export function reasonInNumbers(row, accountsById, cpu, viewerAccountId = null) {
  const reason = row.recycle_reason
  if (!reason || reason === 'WAITING_ON_COUNTERPARTY' || cpu === null || cpu === undefined) return null
  const deliverer = accountsById.get(row.deliverer_account_id)
  const receiver = accountsById.get(row.receiver_account_id)
  if (!(DELIVERER_SIDE.has(reason) ? deliverer : receiver)) return null

  const checks = [
    ...(deliverer ? delivererChecks(row, deliverer, cpu, viewerAccountId) : []),
    ...(receiver ? receiverChecks(row, receiver, cpu, viewerAccountId) : []),
  ]
  const failed = checks.find((check) => !check.passes)
  if (failed) return { verdict: 'fails', reason: failed.reason, text: failed.text, changed: failed.reason !== reason }
  return { verdict: deliverer && receiver ? 'passes' : 'side-passes' }
}

// The deliverer's account as a POST result returns it (camelCase), shaped like a settlement-account
// row, so reasonInNumbers can explain the reason of an order that has just recycled.
export function accountRowFromResult(accountId, account, participantName) {
  return {
    account_id: accountId,
    participant_name: participantName,
    traditional_units: account.traditionalUnits,
    net_cents: account.netCents,
    collateral_value_cents: account.collateralValueCents,
    net_debit_cap_cents: account.netDebitCapCents,
  }
}

// The order in which the engine retries waiting orders (a lab choice): largest amount first, then oldest.
export const byRetryOrder = (a, b) => b.amount_cents - a.amount_cents || a.id - b.id

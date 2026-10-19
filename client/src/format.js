// Display helpers shared by the screens.

const units = new Intl.NumberFormat('en-US')
export const formatUnits = (n) => (n === null || n === undefined ? '—' : units.format(n))

const when = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
export const formatTime = (iso) => when.format(new Date(iso))

const clock = new Intl.DateTimeFormat(undefined, { timeStyle: 'medium' })
export const formatClock = (iso) => clock.format(new Date(iso))

// A business day from the API ("2026-10-19", a DATE) -> "Mon, Oct 19, 2026". Built from its parts:
// new Date("2026-10-19") would read it as midnight UTC and show the previous day west of Greenwich.
const businessDay = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})
export function formatBusinessDate(iso) {
  if (!iso) return '—'
  const [year, month, day] = iso.split('-').map(Number)
  return businessDay.format(new Date(year, month - 1, day))
}

// Money is whole cents everywhere: an integer, never a float. 2000000 -> "$20,000.00",
// -1000000 -> "-$10,000.00". Integer arithmetic only, so every amount this lab can hold is exact.
export function formatCents(cents) {
  if (cents === null || cents === undefined) return '—'
  const abs = Math.abs(cents)
  const rest = abs % 100
  return `${cents < 0 ? '-' : ''}$${units.format((abs - rest) / 100)}.${String(rest).padStart(2, '0')}`
}

// A settlement balance: "+$20,000.00" (credit), "-$10,000.00" (debit), "$0.00" (flat).
export const formatSignedCents = (cents) => (cents > 0 ? `+${formatCents(cents)}` : formatCents(cents))

// Basis points: 500 -> "5%" (a haircut); 12500 -> "1.25" (a factor, 10000 = 1).
export const formatPercentBps = (bps) => `${bps / 100}%`
export const formatFactorBps = (bps) => String(bps / 10000)

export const OPERATION_LABELS = {
  OPENING: 'Opening balance',
  CONVERT_TO_TOKENIZED: 'Convert to tokenized',
  CONVERT_TO_TRADITIONAL: 'Convert to traditional',
  TRANSFER: 'Transfer',
  DELIVER_ORDER: 'Deliver order (DVP)',
  DVP_SETTLEMENT: 'DVP settlement',
  SETTLEMENT_PROGRESS_PAYMENT: 'Settlement progress payment',
  VALUED_CUTOFF: 'Valued cutoff',
  FINAL_SETTLEMENT: 'Final settlement',
}

// Why a deliver order is waiting (recycling), in plain words. A participant sees the other side's
// reason only as WAITING_ON_COUNTERPARTY (a lab choice: each party sees its own reasons).
export const RECYCLE_REASON_LABELS = {
  INSUFFICIENT_POSITION: 'The deliverer does not hold enough traditional units',
  DELIVERER_COLLATERAL_MONITOR: "The deliverer's collateral monitor would go negative",
  RECEIVER_COLLATERAL_MONITOR: "The receiver's collateral monitor would go negative",
  RECEIVER_NET_DEBIT_CAP: "The receiver's debit would exceed its net debit cap",
  WAITING_ON_COUNTERPARTY: 'Waiting on the counterparty (its reason is private to it)',
}

export const INSTRUCTION_STATUS_LABELS = {
  SETTLED: 'Settled',
  RECYCLING: 'Recycling',
  DROPPED_AT_CUTOFF: 'Dropped at cutoff',
}

export const PHASE_LABELS = {
  OPEN: 'Open for valued processing',
  VALUED_CUTOFF_PASSED: 'Valued cutoff passed',
}

// The API's business-rule messages (422/400/403/409), reworded for people.
const FRIENDLY = {
  'insufficient traditional units': 'Not enough traditional units for that quantity.',
  'insufficient tokenized units': 'Not enough tokenized units for that quantity.',
  'wallet is not approved': 'That wallet is not approved, so it cannot send or receive tokenized units.',
  'unknown account': 'That account does not exist.',
  'cannot transfer to yourself': 'Choose another account: you cannot transfer to yourself.',
  'cannot deliver to yourself': 'Choose another account: you cannot deliver to yourself.',
  'valued processing is closed: the valued cutoff has passed':
    'Valued processing is closed for today: the valued cutoff has passed. In this lab, deliver orders and ' +
    'payments are refused until the next business day opens.',
  'the valued cutoff has already run today': 'The valued cutoff has already run for this business day.',
  'run the valued cutoff first': 'Run the valued cutoff first: final settlement comes after it.',
  'collateral monitor would go negative: tokenized units have no collateral value':
    'Refused: your collateral monitor would go negative. Tokenized units have no collateral value, so every ' +
    'unit you mint stops counting as collateral.',
  'net debit cap exceeded': 'Refused by the database: the net debit cap would be exceeded.',
  'collateral monitor would go negative': 'Refused by the database: the collateral monitor would go negative.',
  'participants only': 'Only participants can do that.',
  'operators only': 'Only the operator can do that.',
  'auditors only': 'Only the auditor can do that.',
  'auditors and operators only': 'Only the auditor and the operator can see that.',
  'amountCents must be a whole number of cents 1..10000000000000':
    'The amount must be between $0.01 and $100,000,000,000.00.',
  'idempotency key reused for a different request':
    'That idempotency key was already used for a different request, so nothing was applied.',
}
export const friendlyError = (error) => FRIENDLY[error.message] ?? error.message

export const QUANTITY_MAX = 1_000_000

// Returns an error message, or null when the text is a whole number in 1..QUANTITY_MAX.
export function quantityProblem(text) {
  if (text.trim() === '') return 'Enter a quantity.'
  if (!/^\d+$/.test(text.trim())) return 'Quantity must be a whole number of units.'
  const n = Number(text)
  if (n < 1 || n > QUANTITY_MAX) return `Quantity must be between 1 and ${formatUnits(QUANTITY_MAX)}.`
  return null
}

// The API's range for amountCents: 1 cent .. $100 billion.
export const AMOUNT_MIN_CENTS = 1
export const AMOUNT_MAX_CENTS = 10_000_000_000_000

// Dollars with up to 2 decimals: "20000", "20,000.00", "$0.5". Commas, if used, must group thousands.
const DOLLARS = /^\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?$/

// The typed dollar amount as whole cents, or null if it is not one. No floating point: "0.29" is
// 0 * 100 + 29, never 0.29 * 100 (which is 28.999999999999996).
export function parseDollars(text) {
  const match = DOLLARS.exec(text.trim())
  if (!match) return null
  const dollars = Number(match[1].replace(/,/g, ''))
  const cents = Number((match[2] ?? '').padEnd(2, '0'))
  return dollars * 100 + cents
}

// Returns an error message, or null when the text is a dollar amount in $0.01..$100,000,000,000.00.
export function dollarsProblem(text) {
  if (text.trim() === '') return 'Enter an amount.'
  const cents = parseDollars(text)
  if (cents === null) return 'Enter the amount in dollars, with at most 2 decimals (for example 20,000.00).'
  if (cents < AMOUNT_MIN_CENTS || cents > AMOUNT_MAX_CENTS)
    return `Amount must be between ${formatCents(AMOUNT_MIN_CENTS)} and ${formatCents(AMOUNT_MAX_CENTS)}.`
  return null
}

// "Participant Delta (demo, NOT approved)" -> "Delta"
export const shortName = (account) => account.participant_name.replace(/^Participant /, '').replace(/ \(.*\)$/, '')

// Display helpers shared by the screens.

const units = new Intl.NumberFormat('en-US')
export const formatUnits = (n) => (n === null || n === undefined ? '—' : units.format(n))

const when = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
export const formatTime = (iso) => when.format(new Date(iso))

export const OPERATION_LABELS = {
  OPENING: 'Opening balance',
  CONVERT_TO_TOKENIZED: 'Convert to tokenized',
  CONVERT_TO_TRADITIONAL: 'Convert to traditional',
  TRANSFER: 'Transfer',
}

// The API's business-rule messages (422/400), reworded for people.
const FRIENDLY = {
  'insufficient traditional units': 'Not enough traditional units for that quantity.',
  'insufficient tokenized units': 'Not enough tokenized units for that quantity.',
  'wallet is not approved': 'That wallet is not approved, so it cannot send or receive tokenized units.',
  'unknown account': 'That account does not exist.',
  'cannot transfer to yourself': 'Choose another account: you cannot transfer to yourself.',
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

// "Participant Delta (demo, NOT approved)" -> "Delta"
export const shortName = (account) => account.participant_name.replace(/^Participant /, '').replace(/ \(.*\)$/, '')

import { useState } from 'react'
import { useApiData } from '../useApiData.js'
import { useIdempotentSubmit } from '../useIdempotentSubmit.js'
import { formatUnits, quantityProblem, shortName } from '../format.js'
import { BalanceFigures } from './Balance.jsx'
import { QuantityField, RequestResult } from './Forms.jsx'
import { LoadError, Loading } from './States.jsx'

// Screen 2: the two write actions. Every submit sends a fresh idempotency key; the server
// takes the account to act on from the session, never from this form.
export default function Operate({ user, reloadKey, onChanged }) {
  const holdings = useApiData('/holdings', reloadKey)
  const accounts = useApiData('/accounts')
  const own = holdings.data?.[0]

  return (
    <div className="stack">
      <section className="card" aria-labelledby="balance-title">
        <h2 id="balance-title">Your balance</h2>
        {holdings.loading && !own && <Loading what="your balance" />}
        {holdings.error && !own && <LoadError error={holdings.error} onRetry={holdings.reload} />}
        {own && <BalanceFigures row={own} />}
      </section>
      <div className="grid-2">
        <ConvertForm onChanged={onChanged} />
        <TransferForm user={user} accounts={accounts} onChanged={onChanged} />
      </div>
    </div>
  )
}

function ConvertForm({ onChanged }) {
  const [direction, setDirection] = useState('TO_TOKENIZED')
  const [quantity, setQuantity] = useState('')
  const [problem, setProblem] = useState(null)
  const request = useIdempotentSubmit('/conversions')

  async function handleSubmit(event) {
    event.preventDefault()
    const found = quantityProblem(quantity)
    setProblem(found)
    if (found) return
    if (await request.submit({ direction, quantity: Number(quantity) })) onChanged()
  }

  async function handleResend() {
    if (await request.resend()) onChanged()
  }

  return (
    <section className="card" aria-labelledby="convert-title">
      <h2 id="convert-title">Convert</h2>
      <p className="muted small">
        Change the form of your units. Your total does not change, but your settlement collateral does:
        tokenized units count $0 as collateral.
      </p>
      <form onSubmit={handleSubmit} noValidate>
        <fieldset>
          <legend>Direction</legend>
          <label className="choice">
            <input
              type="radio"
              name="direction"
              value="TO_TOKENIZED"
              checked={direction === 'TO_TOKENIZED'}
              onChange={() => setDirection('TO_TOKENIZED')}
            />
            Traditional → tokenized <span className="muted">(mint)</span>
          </label>
          <label className="choice">
            <input
              type="radio"
              name="direction"
              value="TO_TRADITIONAL"
              checked={direction === 'TO_TRADITIONAL'}
              onChange={() => setDirection('TO_TRADITIONAL')}
            />
            Tokenized → traditional <span className="muted">(burn)</span>
          </label>
        </fieldset>
        <QuantityField id="convert-quantity" value={quantity} onChange={setQuantity} problem={problem} />
        <button type="submit" className="button-primary" disabled={request.state.phase === 'sending'}>
          Convert
        </button>
      </form>
      <RequestResult
        request={request}
        onResend={handleResend}
        describe={(r) =>
          `Converted ${formatUnits(r.quantity)} units to ${r.type === 'CONVERT_TO_TOKENIZED' ? 'tokenized' : 'traditional'}` +
          ` (operation #${r.operationId}). Now ${formatUnits(r.traditionalUnits)} traditional · ` +
          `${formatUnits(r.tokenizedUnits)} tokenized.`
        }
      />
    </section>
  )
}

function TransferForm({ user, accounts, onChanged }) {
  const [to, setTo] = useState('')
  const [quantity, setQuantity] = useState('')
  const [problems, setProblems] = useState({})
  const request = useIdempotentSubmit('/transfers')
  const recipients = (accounts.data ?? []).filter((account) => account.id !== user.account_id)

  async function handleSubmit(event) {
    event.preventDefault()
    const found = { to: to ? null : 'Choose who receives the units.', quantity: quantityProblem(quantity) }
    setProblems(found)
    if (found.to || found.quantity) return
    if (await request.submit({ toAccountId: Number(to), quantity: Number(quantity) })) onChanged()
  }

  async function handleResend() {
    if (await request.resend()) onChanged()
  }

  const recipientName = (id) => {
    const account = accounts.data?.find((a) => a.id === id)
    return account ? `${shortName(account)} (account ${id})` : `account ${id}`
  }

  return (
    <section className="card" aria-labelledby="transfer-title">
      <h2 id="transfer-title">Transfer tokenized units</h2>
      <p className="muted small">Send tokenized units to another approved wallet.</p>
      {accounts.error && !accounts.data && <LoadError error={accounts.error} onRetry={accounts.reload} />}
      <form onSubmit={handleSubmit} noValidate>
        <label htmlFor="transfer-to">To</label>
        <select
          id="transfer-to"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          aria-invalid={Boolean(problems.to)}
          aria-describedby={problems.to ? 'transfer-to-problem' : undefined}
        >
          <option value="">{accounts.loading ? 'Loading accounts…' : 'Choose an account'}</option>
          {recipients.map((account) => (
            <option key={account.id} value={account.id}>
              {account.id} · {shortName(account)} · {account.wallet_ref}
              {account.is_approved ? '' : ' (not approved)'}
            </option>
          ))}
        </select>
        {problems.to && (
          <p id="transfer-to-problem" className="field-problem">
            {problems.to}
          </p>
        )}
        <QuantityField id="transfer-quantity" value={quantity} onChange={setQuantity} problem={problems.quantity} />
        <button type="submit" className="button-primary" disabled={request.state.phase === 'sending'}>
          Transfer
        </button>
      </form>
      <RequestResult
        request={request}
        onResend={handleResend}
        describe={(r) =>
          `Sent ${formatUnits(r.quantity)} tokenized units to ${recipientName(r.destinationAccountId)}` +
          ` (operation #${r.operationId}). Your tokenized balance is now ${formatUnits(r.sourceTokenizedUnits)}.`
        }
      />
    </section>
  )
}

import { useState } from 'react'
import { useApiData } from '../useApiData.js'
import { useIdempotentSubmit } from '../useIdempotentSubmit.js'
import { formatUnits, friendlyError, quantityProblem, QUANTITY_MAX, shortName } from '../format.js'
import { BalanceFigures } from './Balance.jsx'
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
      <p className="muted small">Change the form of your units. Your total does not change.</p>
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

function QuantityField({ id, value, onChange, problem }) {
  return (
    <>
      <label htmlFor={id}>Quantity (units)</label>
      <input
        id={id}
        inputMode="numeric"
        placeholder={`1 to ${formatUnits(QUANTITY_MAX)}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={Boolean(problem)}
        aria-describedby={problem ? `${id}-problem` : undefined}
      />
      {problem && (
        <p id={`${id}-problem`} className="field-problem">
          {problem}
        </p>
      )}
    </>
  )
}

// What happened to the last request, and the safe way to send it again.
function RequestResult({ request, onResend, describe }) {
  const { state, canResend } = request
  if (state.phase === 'idle') return null
  if (state.phase === 'sending') {
    return (
      <p className="message" role="status">
        Sending…
      </p>
    )
  }

  if (state.phase === 'failed') {
    const { error, retryable } = state
    return (
      <div className="message message-bad" role="alert">
        <p>
          {friendlyError(error)} {error.status > 0 && <span className="mono muted">HTTP {error.status}</span>}
        </p>
        {retryable && (
          <>
            <p className="small">
              {error.status === 503
                ? 'Nothing was saved. '
                : 'The request may or may not have reached the server. '}
              Retrying with the same idempotency key is safe: the server applies a key at most once.
            </p>
            <button type="button" className="button-quiet" onClick={onResend} disabled={!canResend}>
              Retry the same request
            </button>
          </>
        )}
      </div>
    )
  }

  return (
    <div className={`message ${state.replayed ? 'message-info' : 'message-ok'}`} role="status">
      {state.replayed && (
        <p>
          <strong>Replayed, not applied twice.</strong> The server already had this idempotency key and
          returned its stored result.
        </p>
      )}
      <p>{describe(state.data)}</p>
      <button type="button" className="button-quiet" onClick={onResend} disabled={!canResend}>
        Send the same request again
      </button>
      <p className="muted small">Same key, same body: the server replays the stored result instead of moving units again.</p>
    </div>
  )
}

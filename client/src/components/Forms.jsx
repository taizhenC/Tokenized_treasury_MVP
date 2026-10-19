import { formatUnits, friendlyError, QUANTITY_MAX } from '../format.js'

// Form pieces shared by the write screens: the quantity and amount inputs, and what happened to the
// last request (with the safe way to send it again).

export function QuantityField({ id, value, onChange, problem }) {
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

// Dollars as typed; the form converts them to whole cents (parseDollars) before sending.
export function AmountField({ id, label = 'Amount (US dollars)', value, onChange, problem }) {
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        inputMode="decimal"
        placeholder="for example 20,000.00"
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
// - describe(data, state): text or elements for a success (state.status is 200 on a replay, else 201/202).
// - tone(data, state): 'ok' (default) or 'warn' for a success that is not final yet. A replay is always 'info'.
export function RequestResult({
  request,
  onResend,
  describe,
  tone,
  replayNote = 'Same key, same body: the server replays the stored result instead of moving units again.',
}) {
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

  const kind = state.replayed ? 'info' : (tone?.(state.data, state) ?? 'ok')
  const body = describe(state.data, state)
  return (
    <div className={`message message-${kind}`} role="status">
      {state.replayed && (
        <p>
          <strong>Replayed, not applied twice.</strong> The server already had this idempotency key and
          returned its stored result.
        </p>
      )}
      {typeof body === 'string' ? <p>{body}</p> : body}
      <button type="button" className="button-quiet" onClick={onResend} disabled={!canResend}>
        Send the same request again
      </button>
      <p className="muted small">{replayNote}</p>
    </div>
  )
}

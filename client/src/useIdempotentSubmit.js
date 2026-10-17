import { useState } from 'react'
import { api } from './api.js'

// A random key that names one request. crypto.randomUUID needs a secure context (https or
// localhost); the fallback covers plain http on another host.
function newKey() {
  if (crypto.randomUUID) return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

// Sends a write (conversion or transfer) with an idempotency key.
// - submit(payload): a NEW request, so a new key.
// - resend(): the exact same payload with the SAME key. The server applies a key at most once,
//   so this is always safe: after a network error or 503 it finishes the request, and after a
//   success it returns the stored result instead of applying it twice (a "replay").
export function useIdempotentSubmit(path) {
  const [state, setState] = useState({ phase: 'idle' }) // idle | sending | done | failed
  const [last, setLast] = useState(null) // { key, payload } of the last request that may be resent

  async function send(payload, key) {
    setState({ phase: 'sending' })
    try {
      const result = await api(path, { method: 'POST', body: { ...payload, idempotencyKey: key } })
      setLast({ key, payload })
      setState({ phase: 'done', ...result })
      return true
    } catch (error) {
      // Only an unanswered request or a 503 is worth retrying with the same key; any other
      // error is a final answer for that exact request.
      const retryable = error.status === 0 || error.status === 503
      setLast(retryable ? { key, payload } : null)
      setState({ phase: 'failed', error, retryable })
      return false
    }
  }

  return {
    state,
    submit: (payload) => send(payload, newKey()),
    resend: () => (last ? send(last.payload, last.key) : Promise.resolve(false)),
    canResend: last !== null && state.phase !== 'sending',
  }
}

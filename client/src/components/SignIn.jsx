import { useState } from 'react'
import { api } from '../api.js'

// The seeded demo users. All five share one public demo password: the data is synthetic.
const DEMO_USERS = [
  { username: 'alpha', note: 'Participant, account 101' },
  { username: 'beta', note: 'Participant, account 102' },
  { username: 'gamma', note: 'Participant, account 103' },
  { username: 'auditor', note: 'Read-only, runs reconciliation' },
  { username: 'operator', note: 'Operator, settlement desk' },
]

// The settlement demo, step by step (its numbers hold on a freshly seeded database).
const SETTLEMENT_DEMO = [
  ['alpha', 'Deliver 200 units to Beta for $20,000.00: it recycles (Beta would be beyond its net debit cap).'],
  ['beta', 'Deliver 100 units to Gamma for $10,000.00: it settles, and the retry also settles the first order.'],
  ['gamma', 'Convert 995 to tokenized: refused (tokenized units count $0 as collateral). Then convert 994.'],
  [
    'alpha',
    'Deliver 100 to Gamma for $10,000.00 (Gamma’s collateral monitor) and 100 to Beta for $10,000.00 ' +
      '(Beta’s cap): both recycle.',
  ],
  ['gamma', 'Pay in $500.00 as a settlement progress payment: the order to Gamma settles.'],
  ['operator', 'Run the valued cutoff (the order to Beta drops), then final settlement: read the NSS file.'],
  ['auditor', 'Run reconciliation: every check passes, still 3,000 units.'],
]
const DEMO_PASSWORD = 'demo-password-1'

export default function SignIn({ onSignedIn, notice }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function handleSubmit(event) {
    event.preventDefault()
    if (!username.trim() || !password) {
      setError('Enter a username and a password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { data } = await api('/login', { method: 'POST', body: { username: username.trim(), password } })
      onSignedIn(data)
    } catch (err) {
      setError(err.status === 401 ? 'Wrong username or password.' : err.message)
      setBusy(false)
    }
  }

  function fillDemoUser(name) {
    setUsername(name)
    setPassword(DEMO_PASSWORD)
    setError(null)
  }

  return (
    <div className="signin">
      <section className="card signin-form" aria-labelledby="signin-title">
        <h2 id="signin-title">Sign in</h2>
        {notice && <p className="message message-warn">{notice}</p>}
        <form onSubmit={handleSubmit} noValidate>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && (
            <p className="message message-bad" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="button-primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </section>

      <section className="card" aria-labelledby="demo-title">
        <h2 id="demo-title">Demo accounts</h2>
        <p className="muted">
          Pick one to fill the form. Every demo user has the password <code>{DEMO_PASSWORD}</code>.
        </p>
        <ul className="demo-users">
          {DEMO_USERS.map((user) => (
            <li key={user.username}>
              <button type="button" className="button-quiet" onClick={() => fillDemoUser(user.username)}>
                <strong>{user.username}</strong>
                <span className="muted">{user.note}</span>
              </button>
            </li>
          ))}
        </ul>
        <details className="demo-script">
          <summary>Settlement demo, step by step</summary>
          <ol>
            {SETTLEMENT_DEMO.map(([who, what], i) => (
              <li key={i}>
                <strong>{who}</strong>: {what}
              </li>
            ))}
          </ol>
          <p className="muted small">
            Deliver orders and payments are on the Settlement (DVP) tab; minting is on Convert &amp; transfer.
          </p>
        </details>
      </section>
    </div>
  )
}

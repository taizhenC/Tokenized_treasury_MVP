import { useState } from 'react'
import { api } from '../api.js'

// The seeded demo users. All four share one public demo password: the data is synthetic.
const DEMO_USERS = [
  { username: 'alpha', note: 'Participant, account 101' },
  { username: 'beta', note: 'Participant, account 102' },
  { username: 'gamma', note: 'Participant, account 103' },
  { username: 'auditor', note: 'Read-only, runs reconciliation' },
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
      </section>
    </div>
  )
}

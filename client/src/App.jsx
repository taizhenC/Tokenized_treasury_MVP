import { useEffect, useState } from 'react'
import { api, setUnauthorizedHandler } from './api.js'
import HealthStatus from './components/HealthStatus.jsx'
import History from './components/History.jsx'
import Holdings from './components/Holdings.jsx'
import Operate from './components/Operate.jsx'
import Reconciliation from './components/Reconciliation.jsx'
import Settlement from './components/Settlement.jsx'
import SettlementDay from './components/SettlementDay.jsx'
import SettlementOverview from './components/SettlementOverview.jsx'
import SignIn from './components/SignIn.jsx'
import './App.css'

// The tabs each role sees; the first one opens after sign-in. The API enforces the same rules;
// hiding a tab is only convenience.
const TABS = {
  PARTICIPANT: [
    ['holdings', 'Holdings'],
    ['operate', 'Convert & transfer'],
    ['settlement', 'Settlement (DVP)'],
    ['history', 'History'],
    ['reconciliation', 'Reconciliation'],
  ],
  AUDITOR: [
    ['holdings', 'Holdings'],
    ['settlement', 'Settlement'],
    ['history', 'History'],
    ['reconciliation', 'Reconciliation'],
  ],
  OPERATOR: [
    ['settlement-day', 'Settlement day'],
    ['holdings', 'Holdings'],
    ['history', 'History'],
  ],
}
const firstTab = (role) => TABS[role]?.[0]?.[0] ?? 'holdings'

function roleNote(user) {
  if (user.role === 'AUDITOR') return 'Auditor, read-only'
  if (user.role === 'OPERATOR') return 'Operator, settlement desk'
  return `Participant · account ${user.account_id}`
}

export default function App() {
  const [session, setSession] = useState({ phase: 'checking' }) // checking | signedOut | signedIn
  const [notice, setNotice] = useState(null)
  const [tab, setTab] = useState('holdings')
  // Bumped after every successful write, so the screens that show balances load them again.
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setSession({ phase: 'signedOut' })
      setNotice('Your session ended. Sign in again.')
    })
    // Is there already a valid session cookie? (The cookie itself is HttpOnly: only the API can read it.)
    api('/me').then(
      ({ data }) => {
        setTab(firstTab(data.role))
        setSession({ phase: 'signedIn', user: data })
      },
      (error) => {
        setSession({ phase: 'signedOut' })
        if (error.status !== 401) setNotice(error.message)
      },
    )
  }, [])

  function handleSignedIn(user) {
    setNotice(null)
    setTab(firstTab(user.role))
    setSession({ phase: 'signedIn', user })
  }

  async function signOut(nextNotice = null) {
    try {
      await api('/logout', { method: 'POST' })
    } catch {
      // Even if the server is unreachable, leave the signed-in screens.
    }
    setNotice(nextNotice)
    setSession({ phase: 'signedOut' })
  }

  const user = session.user
  const tabs = user ? (TABS[user.role] ?? []) : []
  const onChanged = () => setReloadKey((k) => k + 1)

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <h1>Tokenized Treasury Lab</h1>
            <p className="muted small">
              DEMO-TREASURY · simulated custody, conversion, transfer and DVP settlement
            </p>
          </div>
        </div>
        {user && (
          <div className="whoami">
            <span>
              <strong>{user.username}</strong>{' '}
              <span className="muted">{roleNote(user)}</span>
            </span>
            <button type="button" className="button-quiet" onClick={() => signOut()}>
              Sign out
            </button>
          </div>
        )}
      </header>


      {user && (
        <nav className="tabs" aria-label="Screens">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className="tab"
              aria-current={tab === id ? 'page' : undefined}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      )}

      <main>
        {session.phase === 'checking' && <p className="state">Checking your session…</p>}
        {session.phase === 'signedOut' && <SignIn onSignedIn={handleSignedIn} notice={notice} />}
        {user && tab === 'holdings' && <Holdings user={user} reloadKey={reloadKey} />}
        {user && tab === 'operate' && user.role === 'PARTICIPANT' && (
          <Operate user={user} reloadKey={reloadKey} onChanged={onChanged} />
        )}
        {user && tab === 'settlement' && user.role === 'PARTICIPANT' && (
          <Settlement user={user} reloadKey={reloadKey} onChanged={onChanged} />
        )}
        {user && tab === 'settlement' && user.role === 'AUDITOR' && <SettlementOverview reloadKey={reloadKey} />}
        {user && tab === 'settlement-day' && user.role === 'OPERATOR' && (
          <SettlementDay reloadKey={reloadKey} onChanged={onChanged} />
        )}
        {user && tab === 'history' && <History user={user} reloadKey={reloadKey} />}
        {user && tab === 'reconciliation' && (
          <Reconciliation
            user={user}
            onSwitchToAuditor={() => signOut('Signed out. Sign in as auditor to run reconciliation.')}
          />
        )}
      </main>
    </div>
  )
}

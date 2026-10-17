import { useEffect, useState } from 'react'

const CHECK_EVERY_MS = 10_000

// A small live indicator: is the API up, and can it reach the database? Checks repeatedly,
// because in development the UI usually starts a few seconds before the API.
export default function HealthStatus() {
  const [status, setStatus] = useState('checking') // checking | ok | degraded | unreachable

  useEffect(() => {
    let stopped = false
    async function check() {
      let next
      try {
        const res = await fetch('/api/health')
        const body = await res.json()
        next = body.database === 'ok' ? 'ok' : 'degraded'
      } catch {
        next = 'unreachable'
      }
      if (!stopped) setStatus(next)
    }
    check()
    const timer = setInterval(check, CHECK_EVERY_MS)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [])

  const text = {
    checking: 'Checking the server…',
    ok: 'API and database online',
    degraded: 'API online, database unreachable',
    unreachable: 'API unreachable',
  }[status]

  return (
    <p className={`health health-${status}`} role="status">
      <span className="health-dot" aria-hidden="true" />
      {text}
    </p>
  )
}

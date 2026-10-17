import { useEffect, useState } from 'react'
import { api } from './api.js'

// Loads GET /api<path> on mount and again whenever `reloadKey` changes (the app bumps it after
// every successful write) or reload() is called. Keeps the previous data while reloading, so a
// screen does not flash empty.
export function useApiData(path, reloadKey = 0) {
  const [tick, setTick] = useState(0)
  // Names the request that should be on screen. `loading` is simply "the last answer we have
  // belongs to an older request".
  const requestKey = `${path}|${reloadKey}|${tick}`
  const [answer, setAnswer] = useState({ key: null, data: null, error: null, loadedAt: null })

  useEffect(() => {
    let stopped = false
    api(path).then(
      ({ data }) => !stopped && setAnswer({ key: requestKey, data, error: null, loadedAt: new Date() }),
      (error) => !stopped && setAnswer((previous) => ({ ...previous, key: requestKey, error })),
    )
    // Ignore an answer that arrives after the screen has moved on.
    return () => {
      stopped = true
    }
  }, [path, requestKey])

  const current = answer.key === requestKey
  return {
    data: answer.data,
    error: current ? answer.error : null,
    loading: !current,
    loadedAt: answer.loadedAt,
    reload: () => setTick((t) => t + 1),
  }
}

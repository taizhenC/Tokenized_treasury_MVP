// The one place the UI talks to the API. Every call resolves to { data, status, replayed }
// or throws an ApiError whose message is safe to show to the user.

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.error ?? messageForStatus(status))
    this.status = status // 0 = the server could not be reached
    this.body = body
  }
}

function messageForStatus(status) {
  if (status === 0) return 'Cannot reach the server. Check that it is running, then try again.'
  if (status === 503) return 'The database is unavailable. Nothing was saved; try again.'
  return `The request failed (HTTP ${status}).`
}

// Called when a request answers 401 because the session cookie expired or was cleared.
// App registers a handler that returns the user to the sign-in screen. /login and /me are
// excluded: a 401 there just means "not signed in", which App handles itself.
let onUnauthorized = () => {}
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler
}

export async function api(path, { method = 'GET', body } = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: 'same-origin', // send the session cookie
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, null) // network error: the request never got an answer
  }

  let data = null
  if (res.status !== 204) {
    try {
      data = await res.json()
    } catch {
      data = null
    }
  }

  if (!res.ok) {
    // The API always answers errors with JSON. A 5xx without it came from something in
    // front of the API (the dev proxy or the host) because the API itself is not answering.
    if (res.status >= 500 && data === null) throw new ApiError(0, null)
    if (res.status === 401 && path !== '/login' && path !== '/me') onUnauthorized()
    throw new ApiError(res.status, data)
  }
  return { data, status: res.status, replayed: res.headers.get('Idempotency-Replayed') === 'true' }
}

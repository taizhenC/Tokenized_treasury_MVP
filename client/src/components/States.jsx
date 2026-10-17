// Shared loading / error / empty states, so every screen handles them the same way.

export function Loading({ what }) {
  return (
    <p className="state" role="status">
      Loading {what}…
    </p>
  )
}

export function LoadError({ error, onRetry }) {
  return (
    <div className="message message-bad" role="alert">
      <p>{error.message}</p>
      <button type="button" className="button-quiet" onClick={onRetry}>
        Try again
      </button>
    </div>
  )
}

export function Empty({ children }) {
  return <p className="state">{children}</p>
}

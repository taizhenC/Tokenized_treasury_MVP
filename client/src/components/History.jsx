import { useApiData } from '../useApiData.js'
import { formatTime, formatUnits, OPERATION_LABELS, shortName } from '../format.js'
import { Empty, LoadError, Loading } from './States.jsx'

const HISTORY_LIMIT = 100 // the API returns at most this many, newest first

// What one operation did to the signed-in participant's account, as ledger lines would say it.
function effectOn(op, accountId) {
  const q = formatUnits(op.quantity)
  switch (op.operation_type) {
    case 'OPENING':
      return `+${q} traditional`
    case 'CONVERT_TO_TOKENIZED':
      return `−${q} traditional, +${q} tokenized`
    case 'CONVERT_TO_TRADITIONAL':
      return `−${q} tokenized, +${q} traditional`
    case 'TRANSFER':
      return op.source_account_id === accountId ? `−${q} tokenized` : `+${q} tokenized`
    default:
      return ''
  }
}

// Screen 3: the audit trail. A participant sees operations that touched their account; the
// auditor sees every operation.
export default function History({ user, reloadKey }) {
  const operations = useApiData('/operations', reloadKey)
  const accounts = useApiData('/accounts')
  const isAuditor = user.role === 'AUDITOR'

  const accountLabel = (id) => {
    if (id === null) return '—'
    const account = accounts.data?.find((a) => a.id === id)
    const name = account ? shortName(account) : ''
    return (
      <>
        <span className="mono">{id}</span> {name}
      </>
    )
  }

  let body
  if (operations.loading && !operations.data) body = <Loading what="operations" />
  else if (operations.error && !operations.data)
    body = <LoadError error={operations.error} onRetry={operations.reload} />
  else if (operations.data.length === 0)
    body = <Empty>No operations yet. Conversions and transfers appear here as soon as they commit.</Empty>
  else
    body = (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col" className="num">#</th>
              <th scope="col">Operation</th>
              <th scope="col">From</th>
              <th scope="col">To</th>
              <th scope="col" className="num">Units</th>
              {!isAuditor && <th scope="col">Effect on you</th>}
              <th scope="col">By</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {operations.data.map((op) => (
              <tr key={op.id}>
                <td className="num mono">{op.id}</td>
                <td>
                  <span className={`op op-${op.operation_type.toLowerCase()}`}>
                    {OPERATION_LABELS[op.operation_type] ?? op.operation_type}
                  </span>
                </td>
                <td>{accountLabel(op.source_account_id)}</td>
                <td>{accountLabel(op.destination_account_id)}</td>
                <td className="num">{formatUnits(op.quantity)}</td>
                {!isAuditor && <td className="nowrap">{effectOn(op, user.account_id)}</td>}
                <td>{op.actor_username ?? <span className="muted">seed</span>}</td>
                <td className="nowrap">{formatTime(op.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )

  return (
    <section className="card" aria-labelledby="history-title">
      <div className="card-head">
        <div>
          <h2 id="history-title">Operation history</h2>
          <p className="muted">
            {isAuditor ? 'Every committed operation' : 'Operations that touched your account'}, newest first.
            {operations.data?.length === HISTORY_LIMIT && ` Showing the latest ${HISTORY_LIMIT}.`}
          </p>
        </div>
        <button type="button" className="button-quiet" onClick={operations.reload} disabled={operations.loading}>
          Refresh
        </button>
      </div>
      {body}
    </section>
  )
}

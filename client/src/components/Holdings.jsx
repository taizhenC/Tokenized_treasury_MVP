import { useApiData } from '../useApiData.js'
import { formatUnits, shortName } from '../format.js'
import { ApprovalBadge, BalanceFigures, SplitBar } from './Balance.jsx'
import { Empty, LoadError, Loading } from './States.jsx'

// Screen 1: where the synthetic security is held. A participant sees their own account; the
// auditor and the operator see every account (the API decides, from the session, not from the UI).
export default function Holdings({ user, reloadKey }) {
  const { data, error, loading, reload } = useApiData('/holdings', reloadKey)

  if (loading && !data) return <Loading what="holdings" />
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (data.length === 0) return <Empty>No holdings are recorded yet. Seed the database to create them.</Empty>

  return user.role === 'PARTICIPANT' ? <OwnAccount row={data[0]} /> : <AllAccounts rows={data} role={user.role} />
}

function OwnAccount({ row }) {
  return (
    <section className="card" aria-labelledby="own-title">
      <div className="card-head">
        <div>
          <h2 id="own-title">{row.participant_name}</h2>
          <p className="muted mono">
            {row.wallet_ref} · account {row.account_id} · {row.symbol}
          </p>
        </div>
        <ApprovalBadge approved={row.is_approved} />
      </div>
      <BalanceFigures row={row} />
      <SplitBar row={row} />
      <p className="muted small">
        Traditional units are the ordinary book-entry form. Tokenized units are the same holding in the
        simulated token form. Converting changes the form; the total stays the same.
      </p>
    </section>
  )
}

function AllAccounts({ rows, role }) {
  const sum = (key) => rows.reduce((total, row) => total + row[key], 0)
  return (
    <section className="card" aria-labelledby="all-title">
      <div className="card-head">
        <div>
          <h2 id="all-title">All accounts</h2>
          <p className="muted">
            {role === 'OPERATOR' ? 'Operator' : 'Auditor'} view: read-only. {rows[0].symbol}, whole units.
          </p>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Account</th>
              <th scope="col">Wallet</th>
              <th scope="col" className="num">Traditional</th>
              <th scope="col" className="num">Tokenized</th>
              <th scope="col" className="num">Total</th>
              <th scope="col">Split</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.account_id}>
                <td>
                  <span className="mono">{row.account_id}</span> {shortName(row)}
                </td>
                <td>
                  <span className="mono nowrap">{row.wallet_ref}</span> <ApprovalBadge approved={row.is_approved} />
                </td>
                <td className="num">{formatUnits(row.traditional_units)}</td>
                <td className="num">{formatUnits(row.tokenized_units)}</td>
                <td className="num">{formatUnits(row.total_units)}</td>
                <td className="split-cell">
                  <SplitBar row={row} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" colSpan={2}>
                All accounts
              </th>
              <td className="num">{formatUnits(sum('traditional_units'))}</td>
              <td className="num">{formatUnits(sum('tokenized_units'))}</td>
              <td className="num">{formatUnits(sum('total_units'))}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="muted small">
        The total across all accounts must always equal the units held in custody. Reconciliation checks it.
      </p>
    </section>
  )
}

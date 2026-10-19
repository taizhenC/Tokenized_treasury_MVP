import { useApiData } from '../useApiData.js'
import { shortName } from '../format.js'
import { ClosedDays, DaySummary, InstructionsTable, SettlementAccountsTable } from './SettlementParts.jsx'
import { Empty, LoadError, Loading } from './States.jsx'

// Screen: Settlement, for the auditor. Read-only: every settlement account, every deliver order
// with its exact recycle reason, and the closed days (figures and NSS file).
export default function SettlementOverview({ reloadKey }) {
  const day = useApiData('/settlement/day', reloadKey)
  const accounts = useApiData('/settlement/accounts', reloadKey)
  const instructions = useApiData('/delivery-orders', reloadKey)
  const closedDays = useApiData('/settlement/closed-days', reloadKey)
  const directory = useApiData('/accounts')

  const accountsById = new Map((accounts.data ?? []).map((row) => [row.account_id, row]))
  const nameOf = (id) => {
    const account = accountsById.get(id) ?? directory.data?.find((a) => a.id === id)
    return account ? shortName(account) : ''
  }
  const loading = day.loading || accounts.loading || instructions.loading || closedDays.loading

  function reloadAll() {
    day.reload()
    accounts.reload()
    instructions.reload()
    closedDays.reload()
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="overview-title">
        <div className="card-head">
          <div>
            <h2 id="overview-title">Settlement</h2>
            <p className="muted">Auditor view: read-only. Every account, every deliver order, every closed day.</p>
          </div>
          <button type="button" className="button-quiet" onClick={reloadAll} disabled={loading}>
            Refresh
          </button>
        </div>
        {day.loading && !day.data && <Loading what="the settlement day" />}
        {day.error && !day.data && <LoadError error={day.error} onRetry={day.reload} />}
        {day.data && <DaySummary day={day.data} />}
      </section>

      <section className="card" aria-labelledby="ov-accounts-title">
        <h2 id="ov-accounts-title">Settlement accounts</h2>
        <p className="muted small">
          Collateral = traditional units × collateral per unit; tokenized units count $0 (under the SEC staff
          no-action letter of 11 Dec 2025, DTC gives tokenized entitlements no collateral value). Collateral monitor =
          collateral + net, never negative. Net debit cap: no debit beyond it (Delta&apos;s cap is $0, a lab choice so
          that Delta can never be debited; DTC would apply its established minimum net debit cap).
        </p>
        {accounts.loading && !accounts.data && <Loading what="the settlement accounts" />}
        {accounts.error && !accounts.data && <LoadError error={accounts.error} onRetry={accounts.reload} />}
        {accounts.data?.length === 0 && <Empty>No settlement accounts yet. Run the seeds to create them.</Empty>}
        {accounts.data?.length > 0 && <SettlementAccountsTable rows={accounts.data} day={day.data} />}
      </section>

      <section className="card" aria-labelledby="ov-orders-title">
        <h2 id="ov-orders-title">Deliver orders</h2>
        <p className="muted small">
          Newest first, at most 100. The auditor sees every exact recycle reason; each participant sees only the
          reasons about its own side.
        </p>
        {instructions.loading && !instructions.data && <Loading what="the deliver orders" />}
        {instructions.error && !instructions.data && (
          <LoadError error={instructions.error} onRetry={instructions.reload} />
        )}
        {instructions.data?.length === 0 && <Empty>No deliver orders yet.</Empty>}
        {instructions.data?.length > 0 && (
          <InstructionsTable
            rows={instructions.data}
            nameOf={nameOf}
            accountsById={accountsById}
            cpu={day.data?.collateral_cents_per_unit}
          />
        )}
      </section>

      <section className="card" aria-labelledby="ov-closed-title">
        <h2 id="ov-closed-title">Closed days</h2>
        <p className="muted small">The 10 most recent, newest first: each day&apos;s NSS file and settlement figures.</p>
        {closedDays.loading && !closedDays.data && <Loading what="the closed days" />}
        {closedDays.error && !closedDays.data && <LoadError error={closedDays.error} onRetry={closedDays.reload} />}
        {closedDays.data && <ClosedDays days={closedDays.data} nameOf={nameOf} />}
      </section>
    </div>
  )
}

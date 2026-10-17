import { useApiData } from '../useApiData.js'
import { formatUnits } from '../format.js'
import { LoadError, Loading } from './States.jsx'

// Screen 4: the two reconciliation checks. Auditor only: check 2 lists every account's balances.
export default function Reconciliation({ user, onSwitchToAuditor }) {
  if (user.role !== 'AUDITOR') {
    return (
      <section className="card" aria-labelledby="recon-title">
        <h2 id="recon-title">Reconciliation</h2>
        <p>
          Only the auditor can run reconciliation, because the report lists every account&apos;s balances and
          participants may see only their own.
        </p>
        <button type="button" className="button-primary" onClick={onSwitchToAuditor}>
          Sign out and sign in as auditor
        </button>
      </section>
    )
  }
  return <Report />
}

function Result({ ok }) {
  return ok ? <span className="pill pill-ok">Match</span> : <span className="pill pill-bad">Mismatch</span>
}

function Report() {
  const report = useApiData('/reconciliation')
  const data = report.data

  return (
    <div className="stack">
      <section className="card" aria-labelledby="recon-title">
        <div className="card-head">
          <div>
            <h2 id="recon-title">Reconciliation</h2>
            <p className="muted">
              Both checks read one consistent snapshot of the database.
              {report.loadedAt && ` Last run ${report.loadedAt.toLocaleTimeString()}.`}
            </p>
          </div>
          <button type="button" className="button-primary" onClick={report.reload} disabled={report.loading}>
            {report.loading ? 'Running…' : 'Run reconciliation'}
          </button>
        </div>
        {report.loading && !data && <Loading what="the reconciliation report" />}
        {report.error && <LoadError error={report.error} onRetry={report.reload} />}
        {data && (
          <p className={`verdict ${data.passed ? 'verdict-ok' : 'verdict-bad'}`} role="status">
            {data.passed
              ? 'Passed: no units were created or lost, and every balance matches its ledger.'
              : 'Failed: at least one check below does not match. Investigate before trusting the balances.'}
          </p>
        )}
      </section>

      {data && (
        <>
          <section className="card" aria-labelledby="check1-title">
            <h3 id="check1-title">Check 1 · Units are conserved</h3>
            <p className="muted small">
              Traditional plus tokenized units across all accounts must equal the units held in custody.
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Instrument</th>
                    <th scope="col" className="num">In custody</th>
                    <th scope="col" className="num">Traditional</th>
                    <th scope="col" className="num">Tokenized</th>
                    <th scope="col" className="num">Held in total</th>
                    <th scope="col">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {data.conservation.map((row) => (
                    <tr key={row.symbol} className={row.ok ? '' : 'row-bad'}>
                      <td className="mono">{row.symbol}</td>
                      <td className="num">{formatUnits(row.total_custodied_units)}</td>
                      <td className="num">{formatUnits(row.traditional_total)}</td>
                      <td className="num">{formatUnits(row.tokenized_total)}</td>
                      <td className="num">{formatUnits(row.held_total)}</td>
                      <td>
                        <Result ok={row.ok} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card" aria-labelledby="check2-title">
            <h3 id="check2-title">Check 2 · Balances match the ledger</h3>
            <p className="muted small">
              Each stored balance must equal its opening allocation plus every committed ledger change. This
              catches a balance updated on the wrong account, which check 1 alone would miss. It cannot catch a
              wrong recipient that both the ledger and the balances recorded consistently.
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Account</th>
                    <th scope="col">Form</th>
                    <th scope="col" className="num">Stored balance</th>
                    <th scope="col" className="num">From the ledger</th>
                    <th scope="col">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {data.positionsVsLedger.map((row) => (
                    <tr
                      key={`${row.account_id}-${row.asset_id}-${row.representation}`}
                      className={row.ok ? '' : 'row-bad'}
                    >
                      <td className="mono">{row.account_id}</td>
                      <td>{row.representation === 'TOKENIZED' ? 'Tokenized' : 'Traditional'}</td>
                      <td className="num">{formatUnits(row.stored_units)}</td>
                      <td className="num">{formatUnits(row.ledger_units)}</td>
                      <td>
                        <Result ok={row.ok} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

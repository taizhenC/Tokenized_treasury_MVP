import { useApiData } from '../useApiData.js'
import { formatBusinessDate, formatCents, formatSignedCents, formatUnits } from '../format.js'
import { Empty, LoadError, Loading } from './States.jsx'

// Screen 4: the reconciliation checks, units (1-2) and the cash leg (3-8). Auditor only: the report
// lists every account's balances.
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
              Every check reads one consistent snapshot of the database.
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
              ? 'Passed: no units or cents were created or lost, every balance matches its ledger, and the risk controls hold.'
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
              catches a balance updated on the wrong account, which check 1 alone would miss. On its own it cannot
              catch a wrong recipient that both the ledger and the balances recorded consistently; for deliver
              orders, check 6 closes that gap by comparing each settlement with its instruction.
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

          <SettlementChecks data={data} />
        </>
      )}
    </div>
  )
}

// One check: a heading, what it proves, and its rows (or a note when there are none).
function Check({ id, title, intro, empty = null, isEmpty = false, children }) {
  return (
    <section className="card" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      <p className="muted small">{intro}</p>
      {isEmpty && empty ? (
        <Empty>{empty}</Empty>
      ) : (
        <div className="table-wrap">
          <table>{children}</table>
        </div>
      )}
    </section>
  )
}

// Checks 3-8: the cash leg, from the same snapshot, with the same Match/Mismatch pills.
function SettlementChecks({ data }) {
  const cashLines = data.cashLinesNetToZero ?? []
  const settlementVsCash = data.settlementVsCashLedger ?? []
  const collateral = data.collateralVsPositions ?? []
  const instructions = data.settledInstructionsVsLedgers ?? []
  const risk = data.riskControls ?? []
  const closedDays = data.closedDays ?? []

  return (
    <>
      <Check
        id="check3-title"
        title="Check 3 · Every operation's cash lines net to zero"
        intro={
          <>
            No cent is created or lost: the cash ledger lines of each operation (participant balances and DTC&apos;s
            account at the Fed) must add up to $0.00.
          </>
        }
      >
        <thead>
          <tr>
            <th scope="col" className="num">Operations checked</th>
            <th scope="col" className="num">Failing</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {cashLines.map((row, i) => (
            <tr key={i} className={row.ok ? '' : 'row-bad'}>
              <td className="num">{formatUnits(row.operations_checked)}</td>
              <td className="num">{formatUnits(row.failing)}</td>
              <td>
                <Result ok={row.ok} />
              </td>
            </tr>
          ))}
        </tbody>
      </Check>

      <Check
        id="check4-title"
        title="Check 4 · Settlement balances match the cash ledger"
        intro={
          <>
            Each account&apos;s stored net settlement balance must equal the sum of its participant cash lines over every
            day (a closed day&apos;s lines net to zero). An account without a settlement account fails.
          </>
        }
      >
        <thead>
          <tr>
            <th scope="col">Account</th>
            <th scope="col" className="num">Stored net</th>
            <th scope="col" className="num">From the cash ledger</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {settlementVsCash.map((row) => (
            <tr key={row.account_id} className={row.ok ? '' : 'row-bad'}>
              <td className="mono">{row.account_id}</td>
              <td className="num">{formatSignedCents(row.stored_net_cents)}</td>
              <td className="num">{formatSignedCents(row.ledger_net_cents)}</td>
              <td>
                <Result ok={row.ok} />
              </td>
            </tr>
          ))}
        </tbody>
      </Check>

      <Check
        id="check5-title"
        title="Check 5 · Collateral matches positions (tokenized units count $0)"
        intro={
          <>
            Each account&apos;s stored collateral must equal its traditional units × collateral per unit. Tokenized units
            add nothing: the no-action-letter rule, checked against the data. An account without a settlement account
            fails.
          </>
        }
      >
        <thead>
          <tr>
            <th scope="col">Account</th>
            <th scope="col" className="num">Traditional</th>
            <th scope="col" className="num">Tokenized</th>
            <th scope="col" className="num">Per unit</th>
            <th scope="col" className="num">Stored collateral</th>
            <th scope="col" className="num">Expected</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {collateral.map((row) => (
            <tr key={row.account_id} className={row.ok ? '' : 'row-bad'}>
              <td className="mono">{row.account_id}</td>
              <td className="num">{formatUnits(row.traditional_units)}</td>
              <td className="num">{formatUnits(row.tokenized_units)}</td>
              <td className="num">{formatCents(row.collateral_cents_per_unit)}</td>
              <td className="num">{formatCents(row.stored_collateral_cents)}</td>
              <td className="num">{formatCents(row.expected_collateral_cents)}</td>
              <td>
                <Result ok={row.ok} />
              </td>
            </tr>
          ))}
        </tbody>
      </Check>

      <Check
        id="check6-title"
        title="Check 6 · Settled instructions match the ledgers"
        intro={
          <>
            Every settled deliver order must match its DVP settlement (same accounts, units and amount) and have
            exactly its two unit lines and two cash lines, and no DVP settlement may exist without an order. The
            instruction is the independent record that check 2 lacks.
          </>
        }
      >
        <thead>
          <tr>
            <th scope="col" className="num">Settled orders checked</th>
            <th scope="col" className="num">Failing</th>
            <th scope="col" className="num">Settlements without an order</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {instructions.map((row, i) => (
            <tr key={i} className={row.ok ? '' : 'row-bad'}>
              <td className="num">{formatUnits(row.instructions_checked)}</td>
              <td className="num">{formatUnits(row.failing)}</td>
              <td className="num">{formatUnits(row.orphan_settlements)}</td>
              <td>
                <Result ok={row.ok} />
              </td>
            </tr>
          ))}
        </tbody>
      </Check>

      <Check
        id="check7-title"
        title="Check 7 · Risk controls hold"
        intro={
          <>
            Each net debit cap must equal the lower of DTC&apos;s maximum (the lab&apos;s figure, scaled down 1:100,000) and
            average peak × factor; no net may be beyond its cap, and no collateral monitor may be negative. The
            settlement day, every settlement account and every account&apos;s cap inputs must exist.
          </>
        }
      >
        <thead>
          <tr>
            <th scope="col">Account</th>
            <th scope="col" className="num">Net</th>
            <th scope="col" className="num">Stored cap</th>
            <th scope="col" className="num">Expected cap</th>
            <th scope="col" className="num">Collateral monitor</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {risk.map((row) => (
            <tr key={row.account_id} className={row.ok ? '' : 'row-bad'}>
              <td className="mono">{row.account_id}</td>
              <td className="num">{formatSignedCents(row.net_cents)}</td>
              <td className="num">{formatCents(row.net_debit_cap_cents)}</td>
              <td className="num">{formatCents(row.expected_cap_cents)}</td>
              <td className="num">{formatCents(row.collateral_monitor_cents)}</td>
              <td>
                <Result ok={row.ok} />
              </td>
            </tr>
          ))}
        </tbody>
      </Check>

      <Check
        id="check8-title"
        title="Check 8 · Closed days balance"
        intro={
          <>
            For each closed day, the settlement figures, the NSS file and that day&apos;s settlement progress payments must
            be the same amount, and that day&apos;s lines on DTC&apos;s account at the Fed must sum to $0.00. Line by line,
            too: each account&apos;s figure must be what was closed on its balance, and each settling bank&apos;s NSS line and
            closing Fed line must equal the sum of the figures of the accounts it settles for (totals alone would miss
            money shifted from one bank to another).
          </>
        }
        isEmpty={closedDays.length === 0}
        empty="No business day has closed yet: nothing to check."
      >
        <thead>
          <tr>
            <th scope="col">Business day</th>
            <th scope="col" className="num">Figures</th>
            <th scope="col" className="num">NSS file</th>
            <th scope="col" className="num">Progress payments</th>
            <th scope="col" className="num">Fed account lines</th>
            <th scope="col" className="num">Accounts mismatched</th>
            <th scope="col" className="num">Banks mismatched</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {closedDays.map((row) => (
            <tr key={row.business_date} className={row.ok ? '' : 'row-bad'}>
              <td className="nowrap">{formatBusinessDate(row.business_date)}</td>
              <td className="num">{formatSignedCents(row.figures_total_cents)}</td>
              <td className="num">{formatSignedCents(row.nss_total_cents)}</td>
              <td className="num">{formatCents(row.payments_total_cents)}</td>
              <td className="num">{formatSignedCents(row.fed_total_cents)}</td>
              <td className="num">{formatUnits(row.accounts_mismatched)}</td>
              <td className="num">{formatUnits(row.banks_mismatched)}</td>
              <td>
                <Result ok={row.ok} />
              </td>
            </tr>
          ))}
        </tbody>
      </Check>
    </>
  )
}

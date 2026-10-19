import {
  formatBusinessDate,
  formatCents,
  formatClock,
  formatFactorBps,
  formatPercentBps,
  formatSignedCents,
  formatTime,
  formatUnits,
  INSTRUCTION_STATUS_LABELS,
  PHASE_LABELS,
  RECYCLE_REASON_LABELS,
  shortName,
} from '../format.js'
import { capInputs, capUse, LAB_MAXIMUM_NOTE, reasonInNumbers } from '../settlement.js'
import { Empty } from './States.jsx'

// Pieces shared by the three settlement screens (participant, operator, auditor).

export function PhasePill({ phase }) {
  return (
    <span className={`pill ${phase === 'OPEN' ? 'pill-ok' : 'pill-warn'}`}>{PHASE_LABELS[phase] ?? phase}</span>
  )
}

export function StatusPill({ status }) {
  const tone = { SETTLED: 'pill-ok', RECYCLING: 'pill-warn', DROPPED_AT_CUTOFF: 'pill-bad' }[status] ?? ''
  return <span className={`pill ${tone}`}>{INSTRUCTION_STATUS_LABELS[status] ?? status}</span>
}

// "+$20,000.00 credit" / "-$10,000.00 debit" / "$0.00 flat".
export function Net({ cents }) {
  const word = cents > 0 ? 'credit' : cents < 0 ? 'debit' : 'flat'
  return (
    <>
      {formatSignedCents(cents)} <span className="muted small">{word}</span>
    </>
  )
}

// A bar of the debit used against the net debit cap.
export function CapMeter({ netCents, capCents }) {
  const { debit, percent } = capUse(netCents, capCents)
  const level = percent >= 100 ? 'meter-full' : percent >= 80 ? 'meter-high' : ''
  return (
    <div
      className={`meter ${level}`}
      role="img"
      aria-label={`Debit ${formatCents(debit)} of a ${formatCents(capCents)} net debit cap`}
    >
      <span className="meter-fill" style={{ width: `${percent}%` }} />
    </div>
  )
}

// The settlement day: date, phase and the collateral price every control uses.
export function DaySummary({ day }) {
  return (
    <dl className="facts">
      <div>
        <dt>Business day</dt>
        <dd>
          {formatBusinessDate(day.business_date)} <PhasePill phase={day.phase} />
        </dd>
      </div>
      <div>
        <dt>Collateral per unit</dt>
        <dd>
          {formatCents(day.collateral_cents_per_unit)}{' '}
          <span className="muted small">
            prior close {formatCents(day.prior_close_cents)} less a {formatPercentBps(day.haircut_bps)} haircut
          </span>
        </dd>
      </div>
      <div>
        <dt>Maximum net debit cap</dt>
        <dd>
          {formatCents(day.max_net_debit_cap_cents)}{' '}
          <span className="muted small">{LAB_MAXIMUM_NOTE}</span>
        </dd>
      </div>
    </dl>
  )
}

// What a retry needs to happen at all (a lab choice): a credit event, never a mint or burn.
const RETRY_NOTE = 'retries run after a credit: a delivery that settles or a settlement progress payment'

// Why an order is waiting: the reason in words and, when the account it is about is on screen,
// the numbers behind it (every check that can be recomputed from the balances on screen).
export function Reason({ row, accountsById, cpu, viewerAccountId = null }) {
  if (!row.recycle_reason) return <span className="muted">—</span>
  const numbers = row.status === 'RECYCLING' ? reasonInNumbers(row, accountsById, cpu, viewerAccountId) : null
  let text = null
  if (numbers?.verdict === 'passes')
    text = `With the balances now on screen it would pass: it settles at the next retry (${RETRY_NOTE}).`
  else if (numbers?.verdict === 'side-passes')
    text =
      `${viewerAccountId === null ? 'This side' : 'Your side'} would now pass; the order settles at the next ` +
      `retry if the counterparty's side also passes (${RETRY_NOTE}).`
  else if (numbers?.verdict === 'fails')
    text = numbers.changed ? `That control would now pass, but another still fails: ${numbers.text}` : numbers.text
  return (
    <span className="reason">
      {RECYCLE_REASON_LABELS[row.recycle_reason] ?? row.recycle_reason}
      {text && <span className="reason-numbers">{text}</span>}
    </span>
  )
}

export function AccountLabel({ id, nameOf }) {
  if (id === null || id === undefined) return <span className="muted">—</span>
  return (
    <>
      <span className="mono">{id}</span> {nameOf(id)}
    </>
  )
}

// Every settlement account, for the operator and the auditor.
export function SettlementAccountsTable({ rows, day }) {
  const cpu = day?.collateral_cents_per_unit
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Account</th>
            <th scope="col">Settling bank</th>
            <th scope="col" className="num">Traditional</th>
            <th scope="col" className="num">Tokenized</th>
            <th scope="col" className="num">Collateral</th>
            <th scope="col" className="num">Net</th>
            <th scope="col" className="num">Collateral monitor</th>
            <th scope="col" className="num">Net debit cap</th>
            <th scope="col">Debit used</th>
            <th scope="col" className="num">Headroom</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const { raw, capped } = capInputs(row, day?.max_net_debit_cap_cents)
            return (
              <tr key={row.account_id} className={row.collateral_monitor_cents < 0 ? 'row-bad' : ''}>
                <td className="nowrap">
                  <span className="mono">{row.account_id}</span> {shortName(row)}
                </td>
                <td className="nowrap">{row.bank_name ?? <span className="muted">none</span>}</td>
                <td className="num">{formatUnits(row.traditional_units)}</td>
                <td className="num">{formatUnits(row.tokenized_units)}</td>
                <td className="num">
                  {formatCents(row.collateral_value_cents)}
                  {cpu !== undefined && (
                    <span className="cell-note">
                      {formatUnits(row.traditional_units)} × {formatCents(cpu)} · tokenized $0
                    </span>
                  )}
                </td>
                <td className="num">
                  <Net cents={row.net_cents} />
                </td>
                <td className="num">{formatCents(row.collateral_monitor_cents)}</td>
                <td className="num">
                  {formatCents(row.net_debit_cap_cents)}
                  <span className="cell-note">
                    {formatCents(row.avg_top3_peak_cents)} × {formatFactorBps(row.factor_bps)}
                    {capped ? ` = ${formatCents(raw)}, capped` : ''}
                  </span>
                </td>
                <td className="meter-cell">
                  <CapMeter netCents={row.net_cents} capCents={row.net_debit_cap_cents} />
                </td>
                <td className="num">{formatCents(row.headroom_cents)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// Deliver orders, for the operator and the auditor (exact reasons: they see every account).
export function InstructionsTable({ rows, nameOf, accountsById, cpu }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col" className="num">#</th>
            <th scope="col">Day</th>
            <th scope="col">Deliverer</th>
            <th scope="col">Receiver</th>
            <th scope="col" className="num">Units</th>
            <th scope="col" className="num">Amount</th>
            <th scope="col">Status</th>
            <th scope="col">Why it waits</th>
            <th scope="col">Submitted</th>
            <th scope="col">Settled</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="num mono">{row.id}</td>
              <td className="nowrap">{formatBusinessDate(row.business_date)}</td>
              <td className="nowrap">
                <AccountLabel id={row.deliverer_account_id} nameOf={nameOf} />
              </td>
              <td className="nowrap">
                <AccountLabel id={row.receiver_account_id} nameOf={nameOf} />
              </td>
              <td className="num">{formatUnits(row.quantity)}</td>
              <td className="num">{formatCents(row.amount_cents)}</td>
              <td>
                <StatusPill status={row.status} />
              </td>
              <td className="reason-cell">
                <Reason row={row} accountsById={accountsById} cpu={cpu} />
              </td>
              <td className="nowrap">
                <Submitted row={row} />
              </td>
              <td className="nowrap">
                <SettledAt row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Submitted({ row }) {
  return (
    <span title={formatTime(row.submitted_at)}>
      {row.submitted_by} · {formatClock(row.submitted_at)}
    </span>
  )
}

export function SettledAt({ row }) {
  if (!row.settled_at) return <span className="muted">—</span>
  return <span title={formatTime(row.settled_at)}>{formatClock(row.settled_at)}</span>
}

// One closed business day: the NSS file (one net-net figure per settling bank) and each account's
// final figure. Takes the GET shape: { business_date, figures: [...], nss_file: [...] }.
export function ClosedDay({ day, nameOf, headingLevel = 3 }) {
  const Heading = `h${headingLevel}`
  const Sub = `h${headingLevel + 1}`
  const figuresTotal = day.figures.reduce((sum, f) => sum + f.final_net_cents, 0)
  const nssTotal = day.nss_file.reduce((sum, line) => sum + line.net_net_cents, 0)
  return (
    <div className="closed-day">
      <Heading>{formatBusinessDate(day.business_date)}</Heading>
      <div className="grid-2">
        <div className="table-wrap">
          <Sub>NSS file</Sub>
          <table>
            <thead>
              <tr>
                <th scope="col">Settling bank</th>
                <th scope="col" className="num">Net-net</th>
                <th scope="col">Means</th>
              </tr>
            </thead>
            <tbody>
              {day.nss_file.map((line) => (
                <tr key={line.settling_bank_id}>
                  <td className="nowrap">
                    <span className="mono">{line.settling_bank_id}</span> {line.bank_name}
                  </td>
                  <td className="num">{formatSignedCents(line.net_net_cents)}</td>
                  <td className="muted">
                    {line.net_net_cents > 0
                      ? 'The bank is paid'
                      : line.net_net_cents < 0
                        ? 'The bank pays'
                        : 'Nothing moves'}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Total</th>
                <td className="num">{formatSignedCents(nssTotal)}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
        <div className="table-wrap">
          <Sub>Settlement figures</Sub>
          <table>
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col" className="num">Final net</th>
              </tr>
            </thead>
            <tbody>
              {day.figures.map((f) => (
                <tr key={f.account_id}>
                  <td className="nowrap">
                    <AccountLabel id={f.account_id} nameOf={(id) => f.participant_name ? shortName(f) : nameOf(id)} />
                  </td>
                  <td className="num">
                    <Net cents={f.final_net_cents} />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Total</th>
                <td className="num">{formatSignedCents(figuresTotal)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
  )
}

// The closed days, newest first, or a note that none has closed yet.
export function ClosedDays({ days, nameOf, limit }) {
  if (days.length === 0) return <Empty>No business day has closed yet. Final settlement closes one.</Empty>
  return (
    <>
      {days.slice(0, limit ?? days.length).map((day) => (
        <ClosedDay key={day.business_date} day={day} nameOf={nameOf} />
      ))}
      <p className="muted small">
        Both totals are the same number: deliveries net to zero across participants, so what is left is the
        settlement progress payments DTC received that day, already in its account at the Fed.
      </p>
    </>
  )
}

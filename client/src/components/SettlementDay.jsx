import { useApiData } from '../useApiData.js'
import { useIdempotentSubmit } from '../useIdempotentSubmit.js'
import { formatBusinessDate, formatCents, formatSignedCents, shortName } from '../format.js'
import { byRetryOrder } from '../settlement.js'
import { RequestResult } from './Forms.jsx'
import { ClosedDays, DaySummary, InstructionsTable, SettlementAccountsTable } from './SettlementParts.jsx'
import { Empty, LoadError, Loading } from './States.jsx'

const STEP_REPLAY_NOTE = 'Same key, same body: the server replays the stored result instead of running the step twice.'

// Screen: Settlement day, for the operator (the simulated DTC settlement desk). It runs the two
// end-of-day steps, in order, and watches the queue, the accounts and the last closed day.
// The operator holds no account and cannot deliver, convert, transfer or run reconciliation.
export default function SettlementDay({ reloadKey, onChanged }) {
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
  const cpu = day.data?.collateral_cents_per_unit

  function reloadAll() {
    day.reload()
    accounts.reload()
    instructions.reload()
    closedDays.reload()
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="day-title">
        <div className="card-head">
          <div>
            <h2 id="day-title">Settlement day</h2>
            <p className="muted">
              Operator view: the simulated settlement desk. Run the two end-of-day steps in order; the day&apos;s
              figures come from the database, not from this screen.
            </p>
          </div>
          <button type="button" className="button-quiet" onClick={reloadAll} disabled={day.loading}>
            Refresh
          </button>
        </div>
        {day.loading && !day.data && <Loading what="the settlement day" />}
        {day.error && !day.data && <LoadError error={day.error} onRetry={day.reload} />}
        {day.data && <DaySummary day={day.data} />}
      </section>

      <div className="grid-2">
        <CutoffStep day={day} onChanged={onChanged} />
        <FinalStep day={day} nameOf={nameOf} onChanged={onChanged} />
      </div>

      <RecycleQueue instructions={instructions} day={day.data} accountsById={accountsById} cpu={cpu} nameOf={nameOf} />

      <section className="card" aria-labelledby="accounts-title">
        <h2 id="accounts-title">Settlement accounts</h2>
        <p className="muted small">
          Collateral = traditional units × collateral per unit; tokenized units count $0. Headroom is the largest new
          debit an account can take now: the lower of its collateral monitor and its cap plus net. Delta&apos;s cap is
          $0, a lab choice so that Delta can never be debited (DTC would apply its established minimum net debit
          cap).
        </p>
        {accounts.loading && !accounts.data && <Loading what="the settlement accounts" />}
        {accounts.error && !accounts.data && <LoadError error={accounts.error} onRetry={accounts.reload} />}
        {accounts.data?.length === 0 && <Empty>No settlement accounts yet. Run the seeds to create them.</Empty>}
        {accounts.data?.length > 0 && <SettlementAccountsTable rows={accounts.data} day={day.data} />}
      </section>

      <section className="card" aria-labelledby="closed-title">
        <h2 id="closed-title">Latest closed day: NSS file and figures</h2>
        <p className="muted small">
          The NSS file holds one net-net figure per settling bank: the sum of the final figures of the accounts it
          settles for.
        </p>
        {closedDays.loading && !closedDays.data && <Loading what="the closed days" />}
        {closedDays.error && !closedDays.data && <LoadError error={closedDays.error} onRetry={closedDays.reload} />}
        {closedDays.data && <ClosedDays days={closedDays.data} nameOf={nameOf} limit={1} />}
      </section>
    </div>
  )
}

function CutoffStep({ day, onChanged }) {
  const request = useIdempotentSubmit('/settlement-day/valued-cutoff')
  const phase = day.data?.phase
  const ready = phase === 'OPEN' && !day.loading

  async function handleRun() {
    if (await request.submit({})) onChanged()
  }

  async function handleResend() {
    if (await request.resend()) onChanged()
  }

  return (
    <section className="card" aria-labelledby="cutoff-title">
      <h2 id="cutoff-title">Step 1 · Valued cutoff</h2>
      <p className="muted small">
        Ends valued processing for the day: every order still recycling drops and will not settle (at DTC, valued
        items still pending at about 3:10 pm ET drop). After it, this lab refuses new deliver orders and payments.
      </p>
      <button
        type="button"
        className="button-primary"
        onClick={handleRun}
        disabled={!ready || request.state.phase === 'sending'}
      >
        Run valued cutoff
      </button>
      {phase === 'VALUED_CUTOFF_PASSED' && <p className="muted small">Already run for this business day.</p>}
      <RequestResult
        request={request}
        onResend={handleResend}
        replayNote={STEP_REPLAY_NOTE}
        describe={(r) =>
          `Valued cutoff for ${formatBusinessDate(r.businessDate)} (operation #${r.operationId}). ` +
          (r.droppedInstructionIds.length > 0
            ? `Dropped ${r.droppedInstructionIds.map((id) => `#${id}`).join(', ')}: ` +
              `${r.droppedInstructionIds.length === 1 ? 'it' : 'they'} will not settle.`
            : 'Nothing was recycling, so nothing dropped.')
        }
      />
    </section>
  )
}

function FinalStep({ day, nameOf, onChanged }) {
  const request = useIdempotentSubmit('/settlement-day/final-settlement')
  const phase = day.data?.phase
  const ready = phase === 'VALUED_CUTOFF_PASSED' && !day.loading

  async function handleRun() {
    if (await request.submit({})) onChanged()
  }

  async function handleResend() {
    if (await request.resend()) onChanged()
  }

  return (
    <section className="card" aria-labelledby="final-title">
      <h2 id="final-title">Step 2 · Final settlement</h2>
      <p className="muted small">
        Records each account&apos;s net as its final figure, writes the NSS file (one net-net figure per settling bank),
        sets every net to zero and opens the next business day. At DTC: final figures about 3:45 pm ET, the NSS file
        with the Fed about 4:30 pm ET.
      </p>
      <button
        type="button"
        className="button-primary"
        onClick={handleRun}
        disabled={!ready || request.state.phase === 'sending'}
      >
        Run final settlement
      </button>
      {phase === 'OPEN' && <p className="muted small">Available after the valued cutoff.</p>}
      <RequestResult
        request={request}
        onResend={handleResend}
        replayNote={STEP_REPLAY_NOTE}
        describe={(r) => (
          <>
            <p>
              Final settlement for {formatBusinessDate(r.businessDate)} (operation #{r.operationId}). Every net is now
              $0.00, and {formatBusinessDate(r.nextBusinessDate)} is open.
            </p>
            <p>
              <strong>NSS file:</strong>{' '}
              {r.nssFile
                .map((line) =>
                  line.netNetCents > 0
                    ? `${line.bankName} is paid ${formatCents(line.netNetCents)}`
                    : line.netNetCents < 0
                      ? `${line.bankName} pays ${formatCents(-line.netNetCents)}`
                      : `${line.bankName} nets to $0.00`,
                )
                .join('; ')}
              .
            </p>
            <p className="small">
              Figures:{' '}
              {r.figures
                .map((f) => `${nameOf(f.accountId) || f.accountId} ${formatSignedCents(f.finalNetCents)}`)
                .join(', ')}
              .
            </p>
          </>
        )}
      />
    </section>
  )
}

// Orders waiting on a risk control, in the order the engine retries them, plus the ones today's
// valued cutoff dropped (they stay listed until the day closes).
function RecycleQueue({ instructions, day, accountsById, cpu, nameOf }) {
  const all = instructions.data ?? []
  const waiting = all.filter((row) => row.status === 'RECYCLING').sort(byRetryOrder)
  const dropped = all
    .filter((row) => row.status === 'DROPPED_AT_CUTOFF' && row.business_date === day?.business_date)
    .sort((a, b) => a.id - b.id)
  const rows = [...waiting, ...dropped]

  return (
    <section className="card" aria-labelledby="queue-title">
      <div className="card-head">
        <div>
          <h2 id="queue-title">Recycle queue</h2>
          <p className="muted small">
            Orders waiting on a risk control, in the order the engine retries them: largest amount first, then oldest.
            Retries run only when a credit arrives: a delivery settles or a settlement progress payment is received.
            That order and that trigger are lab choices. Orders the valued cutoff dropped today are listed last.
          </p>
        </div>
      </div>
      {instructions.loading && !instructions.data && <Loading what="the recycle queue" />}
      {instructions.error && !instructions.data && (
        <LoadError error={instructions.error} onRetry={instructions.reload} />
      )}
      {instructions.data && rows.length === 0 && <Empty>Nothing is recycling.</Empty>}
      {rows.length > 0 && <InstructionsTable rows={rows} nameOf={nameOf} accountsById={accountsById} cpu={cpu} />}
    </section>
  )
}

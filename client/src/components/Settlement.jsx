import { useState } from 'react'
import { useApiData } from '../useApiData.js'
import { useIdempotentSubmit } from '../useIdempotentSubmit.js'
import {
  dollarsProblem,
  formatBusinessDate,
  formatCents,
  formatFactorBps,
  formatPercentBps,
  formatSignedCents,
  formatUnits,
  parseDollars,
  quantityProblem,
  RECYCLE_REASON_LABELS,
  shortName,
} from '../format.js'
import { accountRowFromResult, capInputs, capUse, reasonInNumbers } from '../settlement.js'
import { AmountField, QuantityField, RequestResult } from './Forms.jsx'
import { CapMeter, PhasePill, Reason, SettledAt, StatusPill, Submitted } from './SettlementParts.jsx'
import { Empty, LoadError, Loading } from './States.jsx'

// Screen: Settlement (DVP), for a participant. Its settlement account and the two risk controls,
// the two cash-leg writes (a deliver order, a settlement progress payment) and its deliver orders.
// Every submit sends a fresh idempotency key; the server takes the account from the session.
export default function Settlement({ user, reloadKey, onChanged }) {
  const accounts = useApiData('/settlement/accounts', reloadKey)
  const day = useApiData('/settlement/day', reloadKey)
  const directory = useApiData('/accounts')
  const instructions = useApiData('/delivery-orders', reloadKey)
  const own = accounts.data?.[0]
  const cpu = day.data?.collateral_cents_per_unit
  const closed = day.data?.phase === 'VALUED_CUTOFF_PASSED'

  // "Beta (account 102)"
  const nameOf = (id) => {
    const account = directory.data?.find((a) => a.id === id)
    return account ? `${shortName(account)} (account ${id})` : `account ${id}`
  }

  // Refresh everything that another participant's request can change: the account card, the day
  // and the instructions (whose "why it waits" numbers are computed from the account card).
  // The account directory does not change during the day.
  const refreshing = accounts.loading || day.loading || instructions.loading
  function reloadAll() {
    accounts.reload()
    day.reload()
    instructions.reload()
  }

  return (
    <div className="stack">
      <AccountCard accounts={accounts} own={own} day={day} />
      <div className="grid-2">
        <DeliverForm
          user={user}
          directory={directory}
          own={own}
          cpu={cpu}
          closed={closed}
          nameOf={nameOf}
          onChanged={onChanged}
        />
        <PaymentForm own={own} closed={closed} onChanged={onChanged} />
      </div>
      <MyInstructions
        user={user}
        instructions={instructions}
        own={own}
        cpu={cpu}
        nameOf={nameOf}
        onRefresh={reloadAll}
        refreshing={refreshing}
      />
    </div>
  )
}

function AccountCard({ accounts, own, day }) {
  let body
  if (accounts.loading && !accounts.data) body = <Loading what="your settlement account" />
  else if (accounts.error && !accounts.data) body = <LoadError error={accounts.error} onRetry={accounts.reload} />
  else if (!own) body = <Empty>No settlement account yet. Run the seeds to create one.</Empty>
  else body = <AccountFigures row={own} day={day.data} />

  return (
    <section className="card" aria-labelledby="settle-title">
      <div className="card-head">
        <div>
          <h2 id="settle-title">Your settlement account</h2>
          <p className="muted">
            {own
              ? `${own.participant_name} · account ${own.account_id} · ` +
                `settles through ${own.bank_name ?? 'no settling bank'}`
              : 'Delivery versus payment: units and money settle together.'}
          </p>
        </div>
        {day.data && (
          <p className="day-badge">
            {formatBusinessDate(day.data.business_date)} <PhasePill phase={day.data.phase} />
          </p>
        )}
      </div>
      {day.error && !day.data && <LoadError error={day.error} onRetry={day.reload} />}
      {body}
    </section>
  )
}

function AccountFigures({ row, day }) {
  const cpu = day?.collateral_cents_per_unit
  const net = row.net_cents
  const { debit } = capUse(net, row.net_debit_cap_cents)
  const { capped } = capInputs(row, day?.max_net_debit_cap_cents)
  return (
    <>
      <dl className="figures figures-4">
        <div>
          <dt>Net settlement balance</dt>
          <dd>{formatSignedCents(net)}</dd>
          <dd className="figure-note">
            {net > 0 ? 'Credit: DTC owes you' : net < 0 ? 'Debit: you pay at the end of the day' : 'Flat: nothing owed'}
          </dd>
        </div>
        <div>
          <dt>Collateral value</dt>
          <dd>{formatCents(row.collateral_value_cents)}</dd>
          <dd className="figure-note">
            {formatUnits(row.traditional_units)} traditional × {cpu === undefined ? '…' : formatCents(cpu)}
            <br />
            {formatUnits(row.tokenized_units)} tokenized × $0.00 <strong>(tokenized units count $0)</strong>
          </dd>
        </div>
        <div>
          <dt>Collateral monitor</dt>
          <dd className={row.collateral_monitor_cents < 0 ? 'figure-bad' : ''}>
            {formatCents(row.collateral_monitor_cents)}
          </dd>
          <dd className="figure-note">
            {formatCents(row.collateral_value_cents)} collateral {net < 0 ? '−' : '+'} {formatCents(Math.abs(net))} net.
            Must not go negative.
          </dd>
        </div>
        <div>
          <dt>Net debit cap</dt>
          <dd>{formatCents(row.net_debit_cap_cents)}</dd>
          <dd className="figure-note">
            Top-3 peak average {formatCents(row.avg_top3_peak_cents)} × {formatFactorBps(row.factor_bps)}
            {capped && `, capped at the lab's ${formatCents(day.max_net_debit_cap_cents)} maximum`}
          </dd>
        </div>
      </dl>
      <div className="cap-line">
        <CapMeter netCents={net} capCents={row.net_debit_cap_cents} />
        <p className="small">
          Debit used: <strong>{formatCents(debit)}</strong> of your {formatCents(row.net_debit_cap_cents)} cap.
          Headroom: <strong>{formatCents(row.headroom_cents)}</strong>, the largest new debit you can take now (the
          lower of your collateral monitor and your cap plus net).
        </p>
      </div>
      <ul className="notes muted small">
        <li>
          Units move one by one in real time; the money is netted and settles at the end of the day. So every
          delivery must keep both sides within DTC&apos;s two main settlement risk controls, or it recycles (waits).
        </li>
        <li>
          <strong>Collateral monitor</strong> = collateral value + net balance. It must not go negative for the
          deliverer or the receiver, so a participant that fails to pay is covered by collateral. Collateral per
          traditional unit: prior close {day ? formatCents(day.prior_close_cents) : '…'} less a{' '}
          {day ? formatPercentBps(day.haircut_bps) : '…'} haircut.
        </li>
        <li>
          <strong>Net debit cap</strong>: your debit may not exceed it. DTC sets it from the average of the 3 highest
          intraday net-debit peaks over a rolling 70 business days, times a factor between 1 and 2, never above
          DTC&apos;s maximum ($3.844B since 6 Apr 2026;{' '}
          {day ? formatCents(day.max_net_debit_cap_cents) : '…'} in this lab, which scales every dollar figure down
          1:100,000).
        </li>
        <li>
          <strong>Tokenized units count $0 as collateral</strong>: under the SEC staff no-action letter of 11 Dec
          2025, DTC gives tokenized entitlements no collateral value. Minting therefore lowers your collateral
          monitor; this lab refuses a mint that would take it below zero.
        </li>
      </ul>
    </>
  )
}

function DeliverForm({ user, directory, own, cpu, closed, nameOf, onChanged }) {
  const [to, setTo] = useState('')
  const [quantity, setQuantity] = useState('')
  const [amount, setAmount] = useState('')
  const [problems, setProblems] = useState({})
  const request = useIdempotentSubmit('/delivery-orders')
  const receivers = (directory.data ?? []).filter((account) => account.id !== user.account_id)

  async function handleSubmit(event) {
    event.preventDefault()
    const found = {
      to: to ? null : 'Choose who receives the units.',
      quantity: quantityProblem(quantity),
      amount: dollarsProblem(amount),
    }
    setProblems(found)
    if (found.to || found.quantity || found.amount) return
    const payload = { toAccountId: Number(to), quantity: Number(quantity), amountCents: parseDollars(amount) }
    if (await request.submit(payload)) onChanged()
  }

  async function handleResend() {
    if (await request.resend()) onChanged()
  }

  // "$20,000.00 for 200 units = $100.00 per unit": a quick check of the typed numbers.
  let pricePerUnit = null
  if (!quantityProblem(quantity) && !dollarsProblem(amount)) {
    const cents = parseDollars(amount)
    const q = Number(quantity)
    const exact = cents % q === 0
    pricePerUnit =
      `${formatCents(cents)} for ${formatUnits(q)} units = ` +
      `${exact ? '' : 'about '}${formatCents(Math.round(cents / q))} per unit.`
  }

  return (
    <section className="card" aria-labelledby="deliver-title">
      <h2 id="deliver-title">Deliver order (DVP)</h2>
      <p className="muted small">
        Deliver traditional units against payment: the units go to the receiver and the amount is credited to your
        settlement balance, in one step. If either side would break a risk control, the order recycles (waits)
        instead of failing.
      </p>
      {closed && (
        <p className="message message-warn">
          The valued cutoff has passed: this lab refuses deliver orders until the next business day opens.
        </p>
      )}
      {directory.error && !directory.data && <LoadError error={directory.error} onRetry={directory.reload} />}
      <form onSubmit={handleSubmit} noValidate>
        <label htmlFor="deliver-to">To</label>
        <select
          id="deliver-to"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          aria-invalid={Boolean(problems.to)}
          aria-describedby={problems.to ? 'deliver-to-problem' : undefined}
        >
          <option value="">{directory.loading ? 'Loading accounts…' : 'Choose an account'}</option>
          {receivers.map((account) => (
            <option key={account.id} value={account.id}>
              {account.id} · {shortName(account)}
            </option>
          ))}
        </select>
        {problems.to && (
          <p id="deliver-to-problem" className="field-problem">
            {problems.to}
          </p>
        )}
        <QuantityField id="deliver-quantity" value={quantity} onChange={setQuantity} problem={problems.quantity} />
        <AmountField id="deliver-amount" value={amount} onChange={setAmount} problem={problems.amount} />
        {pricePerUnit && <p className="muted small">{pricePerUnit}</p>}
        <button type="submit" className="button-primary" disabled={request.state.phase === 'sending'}>
          Send deliver order
        </button>
      </form>
      <RequestResult
        request={request}
        onResend={handleResend}
        tone={(r) => (r.status === 'SETTLED' ? 'ok' : 'warn')}
        describe={(r, state) => (
          <DeliverOutcome r={r} httpStatus={state.status} own={own} cpu={cpu} nameOf={nameOf} />
        )}
        replayNote={
          'Same key, same body: the server replays the stored result (as it was when first sent) ' +
          'instead of delivering again.'
        }
      />
    </section>
  )
}

// What one deliver order did: settled at once (HTTP 201) or recycling (HTTP 202), and why.
function DeliverOutcome({ r, httpStatus, own, cpu, nameOf }) {
  const what = `${formatUnits(r.quantity)} units to ${nameOf(r.toAccountId)} against ${formatCents(r.amountCents)}`
  let numbers = null
  if (r.status === 'RECYCLING' && own) {
    // Only the deliverer's own side can be explained here: a reason about the receiver is masked.
    const order = {
      deliverer_account_id: r.fromAccountId,
      receiver_account_id: r.toAccountId,
      quantity: r.quantity,
      amount_cents: r.amountCents,
      recycle_reason: r.recycleReason,
    }
    const mine = new Map([[r.fromAccountId, accountRowFromResult(r.fromAccountId, r.account, own.participant_name)]])
    numbers = reasonInNumbers(order, mine, cpu, r.fromAccountId)
  }
  return (
    <>
      {r.status === 'SETTLED' ? (
        <p>
          <strong>Settled at once.</strong> <span className="mono muted">HTTP {httpStatus}</span> {what}: instruction
          #{r.instructionId}, DVP settlement operation #{r.settlementOperationId}.
        </p>
      ) : (
        <p>
          <strong>Recycling: nothing moved yet.</strong> <span className="mono muted">HTTP {httpStatus}</span>{' '}
          Instruction #{r.instructionId} ({what}) waits.{' '}
          <strong>{RECYCLE_REASON_LABELS[r.recycleReason] ?? r.recycleReason}.</strong>
          {numbers?.verdict === 'fails' && ` ${numbers.text}`}
        </p>
      )}
      {r.status === 'RECYCLING' && (
        <p className="small">
          It is retried whenever a credit arrives (a delivery settles or a settlement progress payment is received)
          and drops if it is still waiting at the valued cutoff.
        </p>
      )}
      <Released ids={r.releasedInstructionIds} />
      <AccountAfter account={r.account} />
    </>
  )
}

function PaymentForm({ own, closed, onChanged }) {
  const [amount, setAmount] = useState('')
  const [problem, setProblem] = useState(null)
  const request = useIdempotentSubmit('/settlement-progress-payments')

  async function handleSubmit(event) {
    event.preventDefault()
    const found = dollarsProblem(amount)
    setProblem(found)
    if (found) return
    if (await request.submit({ amountCents: parseDollars(amount) })) onChanged()
  }

  async function handleResend() {
    if (await request.resend()) onChanged()
  }

  return (
    <section className="card" aria-labelledby="payment-title">
      <h2 id="payment-title">Settlement progress payment</h2>
      <p className="muted small">
        Wire Fed funds to DTC during the day. The payment credits your settlement balance, which raises your
        collateral monitor and reduces your debit, so orders waiting on you can settle.
      </p>
      {closed && (
        <p className="message message-warn">
          The valued cutoff has passed: this lab refuses payments until the next business day opens.
        </p>
      )}
      <form onSubmit={handleSubmit} noValidate>
        <AmountField id="payment-amount" value={amount} onChange={setAmount} problem={problem} />
        {own && own.net_cents < 0 && (
          <p className="muted small">Your debit now: {formatCents(-own.net_cents)}.</p>
        )}
        <button type="submit" className="button-primary" disabled={request.state.phase === 'sending'}>
          Pay in
        </button>
      </form>
      <RequestResult
        request={request}
        onResend={handleResend}
        describe={(r) => (
          <>
            <p>
              Received {formatCents(r.amountCents)} for {formatBusinessDate(r.businessDate)} (operation #
              {r.operationId}).
            </p>
            <Released ids={r.releasedInstructionIds} none="No waiting order involving you could settle yet." />
            <AccountAfter account={r.account} />
          </>
        )}
        replayNote="Same key, same body: the server replays the stored result instead of crediting the payment twice."
      />
    </section>
  )
}

// The orders the engine settled on its retry pass that involve this participant.
function Released({ ids, none = null }) {
  if (ids.length === 0) return none && <p className="small">{none}</p>
  return (
    <p>
      The engine then retried the waiting orders and also settled{' '}
      <strong>{ids.map((id) => `#${id}`).join(', ')}</strong>, which {ids.length === 1 ? 'involves' : 'involve'}{' '}
      you.
    </p>
  )
}

function AccountAfter({ account }) {
  return (
    <p className="small">
      Your account now: net {formatSignedCents(account.netCents)}, collateral{' '}
      {formatCents(account.collateralValueCents)}, collateral monitor {formatCents(account.collateralMonitorCents)},
      cap {formatCents(account.netDebitCapCents)}, {formatUnits(account.traditionalUnits)} traditional units.
    </p>
  )
}

function MyInstructions({ user, instructions, own, cpu, nameOf, onRefresh, refreshing }) {
  const accountsById = new Map(own ? [[own.account_id, own]] : [])

  let body
  if (instructions.loading && !instructions.data) body = <Loading what="your deliver orders" />
  else if (instructions.error && !instructions.data)
    body = <LoadError error={instructions.error} onRetry={instructions.reload} />
  else if (instructions.data.length === 0)
    body = <Empty>No deliver orders yet. Orders you send or receive appear here.</Empty>
  else
    body = (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col" className="num">#</th>
              <th scope="col">Day</th>
              <th scope="col">You</th>
              <th scope="col">Counterparty</th>
              <th scope="col" className="num">Units</th>
              <th scope="col" className="num">Amount</th>
              <th scope="col">Status</th>
              <th scope="col">Why it waits</th>
              <th scope="col">Submitted</th>
              <th scope="col">Settled</th>
            </tr>
          </thead>
          <tbody>
            {instructions.data.map((row) => {
              const delivering = row.deliverer_account_id === user.account_id
              return (
                <tr key={row.id}>
                  <td className="num mono">{row.id}</td>
                  <td className="nowrap">{formatBusinessDate(row.business_date)}</td>
                  <td className="nowrap">{delivering ? 'Deliver' : 'Receive'}</td>
                  <td className="nowrap">
                    {nameOf(delivering ? row.receiver_account_id : row.deliverer_account_id)}
                  </td>
                  <td className="num">
                    {delivering ? '−' : '+'}
                    {formatUnits(row.quantity)}
                  </td>
                  <td className="num">
                    {delivering ? '+' : '−'}
                    {formatCents(row.amount_cents)}
                  </td>
                  <td>
                    <StatusPill status={row.status} />
                  </td>
                  <td className="reason-cell">
                    <Reason row={row} accountsById={accountsById} cpu={cpu} viewerAccountId={user.account_id} />
                  </td>
                  <td className="nowrap">
                    <Submitted row={row} />
                  </td>
                  <td className="nowrap">
                    <SettledAt row={row} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )

  return (
    <section className="card" aria-labelledby="mine-title">
      <div className="card-head">
        <div>
          <h2 id="mine-title">My instructions</h2>
          <p className="muted">
            Deliver orders you sent or receive, newest first. Units and amount are signed from your side. A reason
            about the other side shows only as &quot;waiting on the counterparty&quot; (a lab choice: each side sees
            its own reasons).
          </p>
        </div>
        <button type="button" className="button-quiet" onClick={onRefresh} disabled={refreshing}>
          Refresh
        </button>
      </div>
      {body}
    </section>
  )
}

import { formatUnits } from '../format.js'

// One account's units in both forms. The total never changes when units are converted.
export function BalanceFigures({ row }) {
  return (
    <dl className="figures">
      <div>
        <dt>
          <span className="swatch swatch-traditional" aria-hidden="true" />
          Traditional
        </dt>
        <dd>{formatUnits(row.traditional_units)}</dd>
      </div>
      <div>
        <dt>
          <span className="swatch swatch-tokenized" aria-hidden="true" />
          Tokenized
        </dt>
        <dd>{formatUnits(row.tokenized_units)}</dd>
      </div>
      <div>
        <dt>Total</dt>
        <dd>{formatUnits(row.total_units)}</dd>
      </div>
    </dl>
  )
}

// A bar split by form: how much of the holding is traditional vs tokenized.
export function SplitBar({ row }) {
  const total = row.traditional_units + row.tokenized_units
  const tokenizedShare = total === 0 ? 0 : (row.tokenized_units / total) * 100
  return (
    <div
      className="splitbar"
      role="img"
      aria-label={`${formatUnits(row.traditional_units)} traditional, ${formatUnits(row.tokenized_units)} tokenized`}
    >
      {total === 0 ? (
        <span className="splitbar-empty" />
      ) : (
        <>
          <span className="splitbar-traditional" style={{ width: `${100 - tokenizedShare}%` }} />
          <span className="splitbar-tokenized" style={{ width: `${tokenizedShare}%` }} />
        </>
      )}
    </div>
  )
}

export function ApprovalBadge({ approved }) {
  return approved ? (
    <span className="pill pill-ok">Approved wallet</span>
  ) : (
    <span className="pill pill-bad">Not approved</span>
  )
}

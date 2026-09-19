import { STATUS_LABEL, formatTime } from '../lib/format.js'

function Confidence({ confidence }) {
  const parts = [
    ['ASR', confidence.asr],
    ['Extraction', confidence.extraction],
    ['Rule', confidence.rule],
  ].filter(([, value]) => value !== null && value !== undefined)
  if (parts.length === 0) return null
  return (
    <ul className="check__confidence" aria-label="Confidence">
      {parts.map(([label, value]) => (
        <li key={label} title={`${label} confidence`}>
          {label} <strong>{value.toFixed(2)}</strong>
        </li>
      ))}
    </ul>
  )
}

/**
 * One check result: what was checked, the verdict and why, expected vs actual, and the
 * transcript evidence. Each evidence line is a button that calls onSeek(evidence).
 */
export default function CheckResult({ result, activeEvidenceKey, onSeek }) {
  const { check, status, reason, expected_value: expected, actual_value: actual, confidence, evidence } = result
  const hasValues = expected !== null || actual !== null

  return (
    <article className={`check check--${status.toLowerCase().replace('_', '-')}`} data-status={status}>
      <header className="check__header">
        <div>
          <h3 className="check__name">{check.name}</h3>
          <code className="check__code">{check.code}</code>
        </div>
        <ul className="check__badges">
          <li className="badge">{check.type}</li>
          <li className={`badge ${check.critical ? 'badge--critical' : ''}`}>
            {check.critical ? 'Critical' : 'Non-critical'}
          </li>
          <li className={`status status--${status.toLowerCase().replace('_', '-')}`}>{STATUS_LABEL[status] ?? status}</li>
        </ul>
      </header>

      <p className="check__reason">{reason}</p>

      {hasValues && (
        <dl className="check__values">
          <div>
            <dt>Expected</dt>
            <dd>{expected ?? '—'}</dd>
          </div>
          <div>
            <dt>Actual (said on the call)</dt>
            <dd>{actual ?? '—'}</dd>
          </div>
        </dl>
      )}

      <Confidence confidence={confidence} />

      <section className="check__evidence" aria-label={`Evidence for ${check.name}`}>
        {evidence.length === 0 ? (
          <p className="check__no-evidence">No transcript evidence for this result.</p>
        ) : (
          <ul>
            {evidence.map((item, index) => {
              const key = `${check.code}:${item.segment_id}:${index}`
              return (
                <li key={key}>
                  <button
                    type="button"
                    className={`evidence ${activeEvidenceKey === key ? 'evidence--active' : ''}`}
                    onClick={() => onSeek(item, key)}
                    aria-label={`Play from ${formatTime(item.start_time)}: ${item.text}`}
                  >
                    <span className="evidence__time">
                      ▶ {formatTime(item.start_time)}–{formatTime(item.end_time)}
                    </span>
                    <span className="evidence__text">{item.text}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </article>
  )
}

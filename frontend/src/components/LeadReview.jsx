import { useCallback, useEffect, useRef, useState } from 'react'
import { audioUrl, getReview } from '../api/client.js'
import { GATE_HINT, GATE_LABEL, STATUS_LABEL, formatBytes, formatDateTime } from '../lib/format.js'
import AudioPlayer from './AudioPlayer.jsx'
import CheckResult from './CheckResult.jsx'
import './review.css'

const STATUS_ORDER = ['FAIL', 'LOW_CONFIDENCE', 'NOT_CHECKABLE', 'PASS']

function GateBanner({ decision, callStatus }) {
  if (!decision) {
    return (
      <section className="gate gate--pending" aria-label="Gate decision">
        <p className="gate__label">No decision yet</p>
        <p className="gate__reason">
          The call is {callStatus === 'FAILED' ? 'marked as failed' : 'still being processed'}; no gate decision has been
          recorded.
        </p>
      </section>
    )
  }
  return (
    <section className={`gate gate--${decision.status.toLowerCase().replace('_', '-')}`} aria-label="Gate decision">
      <p className="gate__label">{GATE_LABEL[decision.status] ?? decision.status}</p>
      <p className="gate__hint">{GATE_HINT[decision.status]}</p>
      <p className="gate__reason">{decision.reason}</p>
      <p className="gate__meta">
        Decided {formatDateTime(decision.decided_at)}
        {decision.check_library_version != null && ` · check library v${decision.check_library_version}`}
      </p>
    </section>
  )
}

function Summary({ data }) {
  const { call, lead, checks } = data
  const recording = call.recording
  return (
    <section className="info" aria-label="Lead and call">
      <dl>
        <div>
          <dt>Lead</dt>
          <dd>{lead.external_lead_id}</dd>
        </div>
        <div>
          <dt>Retailer</dt>
          <dd>{lead.retailer.name}</dd>
        </div>
        <div>
          <dt>Call</dt>
          <dd>
            #{call.id} · {call.status}
          </dd>
        </div>
        <div>
          <dt>Call started</dt>
          <dd>{formatDateTime(call.call_started_at)}</dd>
        </div>
        <div>
          <dt>Recording</dt>
          <dd>{recording ? `${recording.original_filename} (${formatBytes(recording.size_bytes)})` : 'None'}</dd>
        </div>
        <div>
          <dt>Checks</dt>
          <dd>
            {STATUS_ORDER.map((status) => [status, checks.filter((c) => c.status === status).length])
              .filter(([, count]) => count > 0)
              .map(([status, count]) => `${count} ${STATUS_LABEL[status]}`)
              .join(' · ') || 'None'}
          </dd>
        </div>
      </dl>
    </section>
  )
}

/** The TL review page for one call. */
export default function LeadReview({ callId }) {
  // Each piece of state remembers which call it belongs to, so switching calls shows "loading"
  // straight away without resetting state inside the effect.
  const [loaded, setLoaded] = useState({ callId: null })
  const [active, setActive] = useState({ callId: null, key: null })
  const player = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    getReview(callId, { signal: controller.signal })
      .then((data) => setLoaded({ callId, status: 'ready', data }))
      .catch((error) => {
        if (error?.name !== 'AbortError') setLoaded({ callId, status: 'error', message: error.message })
      })
    return () => controller.abort()
  }, [callId])

  const state = loaded.callId === callId ? loaded : { status: 'loading' }
  const activeKey = active.callId === callId ? active.key : null

  // Clicking evidence jumps the player to that moment and starts playback.
  const handleSeek = useCallback(
    (evidence, key) => {
      setActive({ callId, key })
      player.current?.seekAndPlay(evidence.start_time)
    },
    [callId],
  )

  if (state.status === 'loading') return <p className="review__status" role="status">Loading review…</p>
  if (state.status === 'error') return <p className="review__status review__status--error" role="alert">{state.message}</p>

  const { data } = state
  return (
    <main className="review">
      <h1 className="review__title">Call review</h1>
      <GateBanner decision={data.gate_decision} callStatus={data.call.status} />
      <Summary data={data} />

      <div className="review__player">
        {data.call.recording ? (
          <AudioPlayer ref={player} src={audioUrl(callId)} />
        ) : (
          <p className="audio-player__note">There is no recording for this call.</p>
        )}
      </div>

      <section aria-label="Checks">
        <h2 className="review__section">Checks</h2>
        {data.checks.length === 0 ? (
          <p className="review__empty">No checks were evaluated for this call.</p>
        ) : (
          <div className="checks">
            {data.checks.map((result) => (
              <CheckResult
                key={result.check.id}
                result={result}
                activeEvidenceKey={activeKey}
                onSeek={handleSeek}
              />
            ))}
          </div>
        )}
      </section>
    </main>
  )
}

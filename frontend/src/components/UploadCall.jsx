import { useEffect, useRef, useState } from 'react'
import { uploadCall, waitForCall } from '../api/client.js'
import { formatBytes } from '../lib/format.js'
import './upload.css'

/**
 * Upload a call recording, then wait for the pipeline to finish. When it completes,
 * onOpenCall(callId) is called and the app shows the existing review page for that call.
 *
 * States: idle -> uploading -> processing (polling every 2 s, up to 2 minutes) -> completed,
 * or error (the upload failed, the backend reported FAILED, the status check failed, or it
 * is still processing after the time limit). Polling stops on every one of those, and when
 * this component unmounts.
 */
export default function UploadCall({ onOpenCall }) {
  const [retailerCode, setRetailerCode] = useState('')
  const [externalLeadId, setExternalLeadId] = useState('')
  const [file, setFile] = useState(null)
  const [state, setState] = useState({ phase: 'idle' })
  const inFlight = useRef(false) // blocks a second submit before the first re-render disables the button
  const controller = useRef(null)

  // Unmounting cancels the upload or the poll in progress, so nothing keeps running after navigation.
  useEffect(() => () => controller.current?.abort(), [])

  const busy = state.phase === 'uploading' || state.phase === 'processing'
  const canSubmit = retailerCode.trim() !== '' && externalLeadId.trim() !== '' && file !== null

  async function handleSubmit(event) {
    event.preventDefault()
    if (inFlight.current || !canSubmit) return
    inFlight.current = true
    const abort = new AbortController()
    controller.current = abort
    const update = (next) => {
      if (!abort.signal.aborted) setState(next)
    }
    let callId = null
    try {
      update({ phase: 'uploading' })
      const created = await uploadCall({ retailerCode, externalLeadId, audio: file }, { signal: abort.signal })
      callId = created.call_id
      update({ phase: 'processing', callId, attempt: 0 })

      const result = await waitForCall(callId, {
        signal: abort.signal,
        onAttempt: (attempt) => update((s) => ({ ...s, attempt })),
      })
      if (result.outcome === 'completed') {
        update({ phase: 'completed', callId })
        if (!abort.signal.aborted) onOpenCall(callId)
      } else {
        update({ phase: 'error', kind: result.outcome, callId }) // 'failed' or 'timeout'
      }
    } catch (error) {
      if (error?.name === 'AbortError') return
      update({ phase: 'error', kind: callId === null ? 'upload' : 'status', callId, message: error.message })
    } finally {
      inFlight.current = false
    }
  }

  return (
    <main className="upload">
      <h1 className="upload__title">Upload a call recording</h1>

      <form className="upload__form" onSubmit={handleSubmit}>
        <label className="upload__field">
          <span>Retailer code</span>
          <input
            value={retailerCode}
            onChange={(event) => setRetailerCode(event.target.value)}
            placeholder="e.g. retailer1"
            disabled={busy}
            autoComplete="off"
          />
        </label>

        <label className="upload__field">
          <span>External lead ID</span>
          <input
            value={externalLeadId}
            onChange={(event) => setExternalLeadId(event.target.value)}
            placeholder="e.g. 3613790"
            disabled={busy}
            autoComplete="off"
          />
        </label>

        <label className="upload__field">
          <span>Audio recording</span>
          <input
            type="file"
            accept="audio/*"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            disabled={busy}
          />
        </label>
        {file && (
          <p className="upload__file">
            Selected: <strong>{file.name}</strong> ({formatBytes(file.size)})
          </p>
        )}

        <button type="submit" className="upload__submit" disabled={!canSubmit || busy}>
          {state.phase === 'uploading' ? 'Uploading…' : 'Upload Recording'}
        </button>
      </form>

      <Status state={state} fileName={file?.name} onOpenCall={onOpenCall} />
    </main>
  )
}

function Status({ state, fileName, onOpenCall }) {
  switch (state.phase) {
    case 'uploading':
      return <p className="upload__status" role="status">Uploading {fileName}…</p>
    case 'processing':
      return (
        <p className="upload__status" role="status">
          Processing call #{state.callId}… checking every 2 seconds
          {state.attempt > 0 && ` (check ${state.attempt})`}.
        </p>
      )
    case 'completed':
      return <p className="upload__status upload__status--ok" role="status">Processing complete. Opening the review…</p>
    case 'error':
      return (
        <div className="upload__status upload__status--error" role="alert">
          <p>{errorText(state)}</p>
          {state.callId != null && state.kind !== 'upload' && (
            <button type="button" onClick={() => onOpenCall(state.callId)}>
              View call #{state.callId}
            </button>
          )}
        </div>
      )
    default:
      return null
  }
}

function errorText({ kind, callId, message }) {
  switch (kind) {
    case 'upload':
      return `Upload failed: ${message}`
    case 'failed':
      return `Processing failed. Call #${callId} was uploaded, but the pipeline could not finish it.`
    case 'timeout':
      return `Still processing after 2 minutes. Check back later: call #${callId} may still complete.`
    default:
      return `Could not check the status of call #${callId}: ${message}`
  }
}

// Thin client for the QA backend. All calls go to /api/v1 on the same origin (the Vite dev
// server proxies /api to the backend); set VITE_API_BASE to point somewhere else (that needs
// the backend's CORS_ORIGINS to include this app's origin).

const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')
const API = `${BASE}/api/v1`

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** URL of the call recording. The <audio> element streams it and seeks with Range requests. */
export function audioUrl(callId) {
  return `${API}/calls/${encodeURIComponent(callId)}/audio`
}

/** The whole TL review payload for a call: call, lead, gate decision and every check result. */
export function getReview(callId, { signal } = {}) {
  return request(`/calls/${encodeURIComponent(callId)}/review`, {
    signal,
    notFound: `Call ${callId} was not found.`,
  })
}

/** The call itself; `status` is PROCESSING, COMPLETED or FAILED. */
export function getCall(callId, { signal } = {}) {
  return request(`/calls/${encodeURIComponent(callId)}`, { signal, notFound: `Call ${callId} was not found.` })
}

/**
 * Upload a recording. Resolves with { call_id, status } (call_id is an integer, status PROCESSING).
 * The backend sets call_started_at itself, so it is not sent.
 */
export function uploadCall({ retailerCode, externalLeadId, audio }, { signal } = {}) {
  const form = new FormData()
  form.append('retailer_code', retailerCode.trim())
  form.append('external_lead_id', externalLeadId.trim())
  form.append('audio', audio, audio.name)
  // No Content-Type header on purpose: the browser adds the multipart boundary itself.
  return request('/calls', { method: 'POST', body: form, signal })
}

const POLL_INTERVAL_MS = 2000
const POLL_MAX_ATTEMPTS = 60 // 60 x 2 s = 2 minutes

/**
 * Poll the call every `intervalMs` until it stops processing. Resolves with one of:
 *   { outcome: 'completed', call }  the pipeline finished (the call may still be HELD / QA_REVIEW)
 *   { outcome: 'failed', call }     the backend reports FAILED
 *   { outcome: 'timeout' }          still PROCESSING after `maxAttempts` polls
 * Rejects with an AbortError if `signal` aborts, and with an ApiError for a non-retryable error
 * (a 4xx, e.g. the call no longer exists). Network errors and 5xx (e.g. the dev server
 * restarting) are treated as transient and polling continues until the attempts run out.
 */
export async function waitForCall(
  callId,
  { intervalMs = POLL_INTERVAL_MS, maxAttempts = POLL_MAX_ATTEMPTS, signal, onAttempt } = {},
) {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await sleep(intervalMs, signal)
    onAttempt?.(attempt)
    try {
      const call = await getCall(callId, { signal })
      if (call.status === 'COMPLETED') return { outcome: 'completed', call }
      if (call.status === 'FAILED') return { outcome: 'failed', call }
    } catch (error) {
      if (error?.name === 'AbortError') throw error
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) throw error
    }
  }
  return { outcome: 'timeout' }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'))
    const onAbort = () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

async function request(path, { method = 'GET', body, signal, notFound } = {}) {
  let response
  try {
    response = await fetch(`${API}${path}`, { method, body, signal, headers: { Accept: 'application/json' } })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError(0, 'Could not reach the server. Is the backend running?')
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response, notFound))
  }
  return response.json()
}

async function errorMessage(response, notFound) {
  if (response.status === 404 && notFound) return notFound
  try {
    const { detail } = await response.json()
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{ loc: ['body', 'audio'], msg: 'Field required' }, ...]
      return detail.map((d) => `${d.loc?.slice(1).join('.') || 'request'}: ${d.msg}`).join('; ')
    }
  } catch {
    // not JSON: fall through to the generic message
  }
  return `The server returned an error (${response.status}).`
}

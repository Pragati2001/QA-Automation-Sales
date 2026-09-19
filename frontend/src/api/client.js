// Thin client for the QA backend. All calls go to /api/v1 on the same origin (the Vite dev
// server proxies /api to the backend); set VITE_API_BASE to point somewhere else.

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
export async function getReview(callId, { signal } = {}) {
  let response
  try {
    response = await fetch(`${API}/calls/${encodeURIComponent(callId)}/review`, {
      signal,
      headers: { Accept: 'application/json' },
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError(0, 'Could not reach the server. Is the backend running?')
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response, callId))
  }
  return response.json()
}

async function errorMessage(response, callId) {
  if (response.status === 404) return `Call ${callId} was not found.`
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') return body.detail
  } catch {
    // not JSON: fall through to the generic message
  }
  return `The server returned an error (${response.status}).`
}

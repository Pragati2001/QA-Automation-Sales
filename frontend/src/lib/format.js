/** Seconds into the recording as m:ss, or h:mm:ss for calls over an hour: 89.2 -> "1:29". */
export function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

export function formatDateTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? String(iso) : date.toLocaleString()
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export const STATUS_LABEL = {
  PASS: 'PASS',
  FAIL: 'FAIL',
  LOW_CONFIDENCE: 'LOW CONFIDENCE',
  NOT_CHECKABLE: 'NOT CHECKABLE',
}

export const GATE_LABEL = {
  AUTO_PASSED: 'Auto-passed',
  HELD: 'Held',
  QA_REVIEW: 'QA review',
}

export const GATE_HINT = {
  AUTO_PASSED: 'Every critical check passed. The sale goes through.',
  HELD: 'A critical check failed. Held for the team leader.',
  QA_REVIEW: 'The system is not certain enough to decide. A human needs to review this call.',
}

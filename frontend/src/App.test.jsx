import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'

const json = (body, status = 200) => new Response(JSON.stringify(body), { status })

const REVIEW = {
  call: {
    id: 42, status: 'COMPLETED', call_started_at: '2026-03-10T10:30:00+05:30',
    created_at: '2026-03-10T10:40:00+05:30', updated_at: '2026-03-10T10:41:00+05:30',
    recording: { original_filename: 'call.mp3', content_type: 'audio/mpeg', size_bytes: 3 },
  },
  lead: { id: 7, external_lead_id: '3613790', retailer: { id: 1, code: 'retailer1', name: 'Retailer 1' } },
  gate_decision: { status: 'HELD', reason: 'Held: dob_match failed', decided_at: '2026-03-10T10:41:00+05:30', check_library_id: 9, check_library_version: 2 },
  checks: [{
    check: { id: 1, code: 'dob_match', name: 'Date of birth', type: 'FACTUAL', critical: true, version: 2 },
    status: 'FAIL', reason: 'dob mismatch', expected_value: '1991-05-05', actual_value: '1990-01-01',
    confidence: { asr: null, extraction: 0.93, rule: 1, overall: 0.93 },
    evidence: [{ segment_id: '14', text: "It's the first of January, nineteen ninety.", start_time: 21.4, end_time: 24.8 }],
  }],
}

/** Fake backend for the whole flow: upload, status polling and the review. */
function backend(statuses = ['COMPLETED']) {
  let polls = 0
  const handler = vi.fn(async (url, init = {}) => {
    if (init.method === 'POST') return json({ call_id: 42, status: 'PROCESSING' }, 202)
    if (url.endsWith('/calls/42/review')) return json(REVIEW)
    if (url.endsWith('/calls/42')) return json({ id: 42, status: statuses[Math.min(polls++, statuses.length - 1)] })
    return json({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

beforeEach(() => {
  window.history.pushState({}, '', '/')
  vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('starts on the upload page', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Upload a call recording' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Upload Recording' })).toBeTruthy()
  })

  it('opens the existing review straight from its URL', async () => {
    backend()
    window.history.pushState({}, '', '/calls/42/review')
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Checks' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Upload a call recording' })).toBeNull()
  })

  it('an existing call can still be opened by id', async () => {
    backend()
    render(<App />)
    fireEvent.change(screen.getByLabelText(/open an existing call/i), { target: { value: '42' } })
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(await screen.findByRole('heading', { name: 'Checks' })).toBeTruthy()
    expect(window.location.pathname).toBe('/calls/42/review')
  })

  it('uploading a recording ends on the existing review page for that call (gate, checks, evidence, audio)', async () => {
    vi.useFakeTimers()
    const api = backend(['PROCESSING', 'COMPLETED'])
    render(<App />)

    fireEvent.change(screen.getByLabelText('Retailer code'), { target: { value: 'retailer1' } })
    fireEvent.change(screen.getByLabelText('External lead ID'), { target: { value: '3613790' } })
    fireEvent.change(screen.getByLabelText('Audio recording'), { target: { files: [new File(['abc'], 'call.mp3', { type: 'audio/mpeg' })] } })
    fireEvent.submit(screen.getByRole('button', { name: 'Upload Recording' }).closest('form'))
    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    vi.useRealTimers()

    // now on the review page, rendered by the existing components
    expect(await screen.findByRole('heading', { name: 'Checks' })).toBeTruthy()
    expect(window.location.pathname).toBe('/calls/42/review')
    const gate = screen.getByRole('region', { name: 'Gate decision' })
    expect(gate.textContent).toContain('Held')
    expect(gate.textContent).toContain('dob_match failed')
    expect(screen.getByRole('article').getAttribute('data-status')).toBe('FAIL')
    expect(screen.getByRole('button', { name: /Play from 0:21/ })).toBeTruthy() // evidence
    expect(document.querySelector('audio').getAttribute('src')).toBe('/api/v1/calls/42/audio')

    // one upload, the status polls, and the review fetched once
    const urls = api.mock.calls.map(([url, init]) => `${init?.method ?? 'GET'} ${url}`)
    expect(urls.filter((u) => u === 'POST /api/v1/calls')).toHaveLength(1)
    expect(urls.filter((u) => u === 'GET /api/v1/calls/42')).toHaveLength(2)
    expect(urls.filter((u) => u === 'GET /api/v1/calls/42/review')).toHaveLength(1)
  })

  it('the review reached by uploading still seeks and plays when evidence is clicked', async () => {
    backend()
    window.history.pushState({}, '', '/calls/42/review')
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /Play from 0:21/ }))
    expect(document.querySelector('audio').currentTime).toBe(21.4)
  })
})

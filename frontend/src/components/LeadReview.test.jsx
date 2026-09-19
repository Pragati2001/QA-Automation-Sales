import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LeadReview from './LeadReview.jsx'

const check = (over) => ({
  check: { id: 1, code: 'c', name: 'A check', type: 'FACTUAL', critical: true, version: 3 },
  status: 'PASS',
  reason: 'looks fine',
  expected_value: null,
  actual_value: null,
  confidence: { asr: null, extraction: null, rule: null, overall: null },
  evidence: [],
  ...over,
})

const PAYLOAD = {
  call: {
    id: 42,
    status: 'COMPLETED',
    call_started_at: '2026-03-10T10:30:00+05:30',
    created_at: '2026-03-10T10:40:00+05:30',
    updated_at: '2026-03-10T10:41:00+05:30',
    recording: { original_filename: 'call.wav', content_type: 'audio/wav', size_bytes: 32044 },
  },
  lead: { id: 7, external_lead_id: '3613790', retailer: { id: 1, code: 'retailer1', name: 'Retailer 1' } },
  gate_decision: {
    status: 'HELD',
    reason: 'Held: critical check(s) failed: dob_match. Checks evaluated: 4 (1 FAIL)',
    decided_at: '2026-03-10T10:41:00+05:30',
    check_library_id: 9,
    check_library_version: 3,
  },
  checks: [
    check({
      check: { id: 1, code: 'dob_match', name: 'Date of birth', type: 'FACTUAL', critical: true, version: 3 },
      status: 'FAIL',
      reason: 'dob mismatch: said 1990-01-01, expected 1991-05-05',
      expected_value: '1991-05-05',
      actual_value: 'the first of january nineteen ninety',
      confidence: { asr: null, extraction: 0.93, rule: 1.0, overall: 0.93 },
      evidence: [{ segment_id: '14', text: "It's the first of January, nineteen ninety.", start_time: 21.4, end_time: 24.8 }],
    }),
    check({
      check: { id: 2, code: 'email_match', name: 'Email', type: 'FACTUAL', critical: true, version: 3 },
      status: 'LOW_CONFIDENCE',
      reason: 'Transcript confidence 0.62 is below the 0.70 threshold',
      expected_value: 'synthetic.test@example.com',
      actual_value: 'synthetic.test@example.com',
      evidence: [
        { segment_id: '19', text: "It's synthetic dot test at example dot com.", start_time: 89.2, end_time: 93.1 },
        { segment_id: '20', text: 'Let me read that back to you.', start_time: 93.8, end_time: 100.2 },
      ],
    }),
    check({
      check: { id: 3, code: 'rate_match', name: 'Peak rate', type: 'FACTUAL', critical: true, version: 3 },
      status: 'NOT_CHECKABLE',
      reason: 'Expected value not available: CRM.peak_rate is missing',
    }),
    check({
      check: { id: 4, code: 'recording_disclaimer', name: 'Recording disclaimer', type: 'VERBATIM', critical: false, version: 3 },
      status: 'PASS',
      reason: 'All 1 required phrase(s) said by the agent',
      expected_value: 'this call is recorded',
      actual_value: 'this call is recorded',
      confidence: { asr: 0.97, extraction: null, rule: null, overall: 0.97 },
      evidence: [{ segment_id: '12', text: 'Just so you know, this call is recorded.', start_time: 0.0, end_time: 7.4 }],
    }),
  ],
}

function respondWith(body, status = 200) {
  return vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

let playSpy

beforeEach(() => {
  // jsdom doesn't implement media playback
  playSpy = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
  vi.stubGlobal('fetch', respondWith(PAYLOAD))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function renderReview(callId = '42') {
  render(<LeadReview callId={callId} />)
  await screen.findByRole('heading', { name: 'Checks' })
}

describe('loading the review', () => {
  it('requests the review API for the given call id', async () => {
    await renderReview('42')
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/calls/42/review')
  })

  it('shows a loading state first', () => {
    render(<LeadReview callId="42" />)
    expect(screen.getByRole('status').textContent).toMatch(/loading/i)
  })

  it('shows a clear message when the call does not exist', async () => {
    vi.stubGlobal('fetch', respondWith({ detail: 'Call 99 not found' }, 404))
    render(<LeadReview callId="99" />)
    expect((await screen.findByRole('alert')).textContent).toBe('Call 99 was not found.')
  })

  it('shows a message when the server cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    render(<LeadReview callId="42" />)
    expect((await screen.findByRole('alert')).textContent).toMatch(/could not reach the server/i)
  })

  it('reloads when the call id changes', async () => {
    const { rerender } = render(<LeadReview callId="42" />)
    await screen.findByRole('heading', { name: 'Checks' })
    rerender(<LeadReview callId="43" />)
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls[1][0]).toBe('/api/v1/calls/43/review')
  })
})

describe('gate decision, lead and call', () => {
  it('shows the gate decision prominently with its reasoning', async () => {
    await renderReview()
    const gate = screen.getByRole('region', { name: 'Gate decision' })
    expect(gate.className).toContain('gate--held')
    expect(within(gate).getByText('Held').className).toBe('gate__label')
    expect(gate.textContent).toContain('critical check(s) failed: dob_match')
    expect(gate.textContent).toContain('check library v3')
  })

  it.each([
    ['AUTO_PASSED', 'Auto-passed', 'gate--auto-passed'],
    ['QA_REVIEW', 'QA review', 'gate--qa-review'],
  ])('renders %s', async (status, label, cssClass) => {
    vi.stubGlobal('fetch', respondWith({ ...PAYLOAD, gate_decision: { ...PAYLOAD.gate_decision, status } }))
    await renderReview()
    const gate = screen.getByRole('region', { name: 'Gate decision' })
    expect(gate.className).toContain(cssClass)
    expect(within(gate).getByText(label)).toBeTruthy()
  })

  it('says so when there is no decision yet', async () => {
    vi.stubGlobal('fetch', respondWith({ ...PAYLOAD, gate_decision: null, checks: [] }))
    await renderReview()
    const gate = screen.getByRole('region', { name: 'Gate decision' })
    expect(gate.textContent).toContain('No decision yet')
    expect(gate.textContent).toMatch(/still being processed/)
  })

  it('shows the lead, retailer, call and recording', async () => {
    await renderReview()
    const info = screen.getByRole('region', { name: 'Lead and call' }).textContent
    expect(info).toContain('3613790')
    expect(info).toContain('Retailer 1')
    expect(info).toContain('#42')
    expect(info).toContain('COMPLETED')
    expect(info).toContain('call.wav')
    expect(info).toContain('1 FAIL · 1 LOW CONFIDENCE · 1 NOT CHECKABLE · 1 PASS')
  })
})

describe('the checks', () => {
  it('lists every check with its status, in the order the API returned them', async () => {
    await renderReview()
    const articles = screen.getAllByRole('article')
    expect(articles.map((a) => a.getAttribute('data-status'))).toEqual(['FAIL', 'LOW_CONFIDENCE', 'NOT_CHECKABLE', 'PASS'])
    const labels = articles.map((a) => a.querySelector('.status').textContent)
    expect(labels).toEqual(['FAIL', 'LOW CONFIDENCE', 'NOT CHECKABLE', 'PASS'])
  })

  it('shows name, code, type and criticality', async () => {
    await renderReview()
    const dob = screen.getAllByRole('article')[0]
    expect(within(dob).getByRole('heading', { name: 'Date of birth' })).toBeTruthy()
    expect(within(dob).getByText('dob_match')).toBeTruthy()
    expect(within(dob).getByText('FACTUAL')).toBeTruthy()
    expect(within(dob).getByText('Critical')).toBeTruthy()
    expect(within(screen.getAllByRole('article')[3]).getByText('Non-critical')).toBeTruthy()
  })

  it('shows the reason, and expected and actual values when there are any', async () => {
    await renderReview()
    const [dob, , rate] = screen.getAllByRole('article')
    expect(dob.textContent).toContain('dob mismatch: said 1990-01-01, expected 1991-05-05')
    expect(dob.querySelector('.check__values').textContent).toContain('1991-05-05')
    expect(dob.querySelector('.check__values').textContent).toContain('the first of january nineteen ninety')
    expect(rate.textContent).toContain('CRM.peak_rate is missing')
    expect(rate.querySelector('.check__values')).toBeNull() // nothing to compare
  })

  it('shows the confidence values that exist', async () => {
    await renderReview()
    const dob = screen.getAllByRole('article')[0]
    expect(within(dob).getByLabelText('Confidence').textContent).toBe('Extraction 0.93Rule 1.00')
    expect(screen.getAllByRole('article')[2].querySelector('.check__confidence')).toBeNull()
  })

  it('shows evidence text with its timestamps, and says when there is none', async () => {
    await renderReview()
    const [dob, email, rate] = screen.getAllByRole('article')
    const evidence = within(dob).getByRole('button')
    expect(evidence.textContent).toContain("It's the first of January, nineteen ninety.")
    expect(evidence.textContent).toContain('0:21–0:24')
    expect(within(email).getAllByRole('button')).toHaveLength(2)
    expect(within(email).getAllByRole('button')[1].textContent).toContain('1:33–1:40')
    expect(rate.textContent).toContain('No transcript evidence')
  })

  it('says so when no checks were evaluated', async () => {
    vi.stubGlobal('fetch', respondWith({ ...PAYLOAD, checks: [] }))
    await renderReview()
    expect(screen.getByText('No checks were evaluated for this call.')).toBeTruthy()
  })
})

describe('the audio player and evidence interaction', () => {
  const audioEl = () => document.querySelector('audio')

  it('renders a player pointing at the call audio', async () => {
    await renderReview()
    expect(audioEl().getAttribute('src')).toBe('/api/v1/calls/42/audio')
    expect(audioEl().hasAttribute('controls')).toBe(true)
  })

  it('has no player, and says so, when the call has no recording', async () => {
    vi.stubGlobal('fetch', respondWith({ ...PAYLOAD, call: { ...PAYLOAD.call, recording: null } }))
    await renderReview()
    expect(audioEl()).toBeNull()
    expect(screen.getByText('There is no recording for this call.')).toBeTruthy()
  })

  it('clicking evidence seeks the player to its start time and starts playback', async () => {
    await renderReview()
    expect(playSpy).not.toHaveBeenCalled()

    fireEvent.click(within(screen.getAllByRole('article')[0]).getByRole('button'))

    expect(audioEl().currentTime).toBe(21.4)
    expect(playSpy).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Playing from 0:21')).toBeTruthy()
  })

  it('each evidence item seeks to its own start time', async () => {
    await renderReview()
    const email = screen.getAllByRole('article')[1]
    const [first, second] = within(email).getAllByRole('button')

    fireEvent.click(first)
    expect(audioEl().currentTime).toBe(89.2)
    fireEvent.click(second)
    expect(audioEl().currentTime).toBe(93.8)
    fireEvent.click(within(screen.getAllByRole('article')[3]).getByRole('button')) // starts at 0
    expect(audioEl().currentTime).toBe(0)
    expect(playSpy).toHaveBeenCalledTimes(3)
  })

  it('clicking the same evidence again seeks back to it', async () => {
    await renderReview()
    const button = within(screen.getAllByRole('article')[0]).getByRole('button')
    fireEvent.click(button)
    audioEl().currentTime = 60 // the user listened on
    fireEvent.click(button)
    expect(audioEl().currentTime).toBe(21.4)
    expect(playSpy).toHaveBeenCalledTimes(2)
  })

  it('highlights the evidence that was last played', async () => {
    await renderReview()
    const [dob, email] = screen.getAllByRole('article')
    const dobButton = within(dob).getByRole('button')
    const emailButton = within(email).getAllByRole('button')[1]

    fireEvent.click(dobButton)
    expect(dobButton.className).toContain('evidence--active')
    fireEvent.click(emailButton)
    expect(dobButton.className).not.toContain('evidence--active')
    expect(emailButton.className).toContain('evidence--active')
  })

  it('tells the user when the browser blocks playback', async () => {
    playSpy.mockRejectedValue(new DOMException('blocked', 'NotAllowedError'))
    await renderReview()
    fireEvent.click(within(screen.getAllByRole('article')[0]).getByRole('button'))
    expect(await screen.findByText(/browser blocked playback/i)).toBeTruthy()
    expect(audioEl().currentTime).toBe(21.4) // still positioned, so the play button works
  })

  it('tells the user when the recording cannot be loaded', async () => {
    await renderReview()
    fireEvent.error(audioEl())
    expect(await screen.findByText('The recording could not be loaded.')).toBeTruthy()
  })
})

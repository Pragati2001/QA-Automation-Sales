import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UploadCall from './UploadCall.jsx'

const json = (body, status = 200) => new Response(JSON.stringify(body), { status })

/** A fake backend: POST /calls, then GET /calls/42 answering from `statuses` (the last one repeats). */
function backend({ statuses = ['COMPLETED'], upload } = {}) {
  let polls = 0
  const handler = vi.fn(async (url, init = {}) => {
    if (init.method === 'POST') return upload ? upload(init) : json({ call_id: 42, status: 'PROCESSING' }, 202)
    const status = statuses[Math.min(polls++, statuses.length - 1)]
    if (status instanceof Error) throw status
    if (typeof status === 'number') return json({ detail: 'error' }, status)
    return json({ id: 42, status })
  })
  vi.stubGlobal('fetch', handler)
  return {
    handler,
    posts: () => handler.mock.calls.filter(([, init]) => init?.method === 'POST'),
    polls: () => handler.mock.calls.filter(([, init]) => init?.method !== 'POST'),
  }
}

let onOpenCall

beforeEach(() => {
  vi.useFakeTimers()
  onOpenCall = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const audio = (name = 'call.mp3', bytes = 3) => new File([new Uint8Array(bytes)], name, { type: 'audio/mpeg' })

function fill({ retailer = 'retailer1', lead = '3613790', file = audio() } = {}) {
  if (retailer !== null) fireEvent.change(screen.getByLabelText('Retailer code'), { target: { value: retailer } })
  if (lead !== null) fireEvent.change(screen.getByLabelText('External lead ID'), { target: { value: lead } })
  if (file !== null) fireEvent.change(screen.getByLabelText('Audio recording'), { target: { files: [file] } })
}

const submit = () => fireEvent.submit(screen.getByRole('button', { name: /upload recording|uploading/i }).closest('form'))
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

describe('the form', () => {
  it('has the retailer code, lead id and an audio-only file picker', () => {
    render(<UploadCall onOpenCall={onOpenCall} />)
    expect(screen.getByLabelText('Retailer code')).toBeTruthy()
    expect(screen.getByLabelText('External lead ID')).toBeTruthy()
    expect(screen.getByLabelText('Audio recording').getAttribute('accept')).toBe('audio/*')
    expect(screen.getByRole('button', { name: 'Upload Recording' })).toBeTruthy()
  })

  it('needs all three inputs before the button is enabled', () => {
    render(<UploadCall onOpenCall={onOpenCall} />)
    const button = screen.getByRole('button', { name: 'Upload Recording' })
    expect(button.disabled).toBe(true)
    fill({ retailer: 'retailer1', lead: null, file: null })
    expect(button.disabled).toBe(true)
    fill({ retailer: null, lead: '1', file: null })
    expect(button.disabled).toBe(true)
    fill({ retailer: null, lead: null })
    expect(button.disabled).toBe(false)
  })

  it('a blank retailer code (spaces only) does not count', () => {
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill({ retailer: '   ' })
    expect(screen.getByRole('button', { name: 'Upload Recording' }).disabled).toBe(true)
  })

  it('shows the selected filename and its size', () => {
    render(<UploadCall onOpenCall={onOpenCall} />)
    expect(screen.queryByText(/Selected:/)).toBeNull()
    fill({ file: audio('interview.wav', 2 * 1024 * 1024) })
    const line = screen.getByText(/Selected:/)
    expect(line.textContent).toBe('Selected: interview.wav (2.0 MB)')
  })

  it('formats small and medium sizes', () => {
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill({ file: audio('a.mp3', 512) })
    expect(screen.getByText(/Selected:/).textContent).toContain('512 B')
    fill({ retailer: null, lead: null, file: audio('b.mp3', 5 * 1024) })
    expect(screen.getByText(/Selected:/).textContent).toContain('5.0 KB')
  })
})

describe('uploading', () => {
  it('posts the confirmed field names and sends no call_started_at or Content-Type', async () => {
    const api = backend({ statuses: ['PROCESSING'] })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill({ retailer: ' retailer1 ', lead: '3613790', file: audio('call.mp3') })
    submit()
    await advance(0)

    expect(api.posts()).toHaveLength(1)
    const [url, init] = api.posts()[0]
    expect(url).toBe('/api/v1/calls')
    expect([...init.body.keys()].sort()).toEqual(['audio', 'external_lead_id', 'retailer_code'])
    expect(init.body.get('retailer_code')).toBe('retailer1')
    expect(init.body.get('external_lead_id')).toBe('3613790')
    expect(init.body.get('audio').name).toBe('call.mp3')
    expect(Object.keys(init.headers).map((h) => h.toLowerCase())).not.toContain('content-type')
  })

  it('disables the button and the inputs while uploading', async () => {
    let finishUpload
    backend({ statuses: ['PROCESSING'], upload: () => new Promise((resolve) => { finishUpload = resolve }) })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(0)

    const button = screen.getByRole('button', { name: 'Uploading…' })
    expect(button.disabled).toBe(true)
    expect(screen.getByLabelText('Retailer code').disabled).toBe(true)
    expect(screen.getByLabelText('Audio recording').disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toBe('Uploading call.mp3…')

    finishUpload(json({ call_id: 42, status: 'PROCESSING' }, 202))
    await advance(0)
    expect(screen.getByRole('status').textContent).toMatch(/Processing call #42/)
  })

  it('a double submit sends only one upload', async () => {
    const api = backend({ statuses: ['PROCESSING'] })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    submit() // before React has re-rendered the disabled button
    await advance(0)
    submit() // and again while processing
    await advance(0)

    expect(api.posts()).toHaveLength(1)
  })

  it('an upload error is shown with the server message, and the form can be used again', async () => {
    const api = backend({ upload: () => json({ detail: "Unknown retailer_code 'nope'" }, 404) })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill({ retailer: 'nope' })
    submit()
    await advance(0)

    expect(screen.getByRole('alert').textContent).toBe("Upload failed: Unknown retailer_code 'nope'")
    expect(screen.queryByRole('button', { name: /View call/ })).toBeNull() // no call was created
    expect(screen.getByRole('button', { name: 'Upload Recording' }).disabled).toBe(false)
    expect(api.polls()).toHaveLength(0)
    expect(onOpenCall).not.toHaveBeenCalled()

    await advance(30000)
    expect(api.polls()).toHaveLength(0) // never polls a call that was not created
  })

  it('an unreachable server during upload is reported', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(0)
    expect(screen.getByRole('alert').textContent).toMatch(/Upload failed: Could not reach the server/)
  })

  it('a failed upload can be retried', async () => {
    let attempt = 0
    const api = backend({ upload: () => (attempt++ === 0 ? json({ detail: 'temporary' }, 500) : json({ call_id: 42, status: 'PROCESSING' }, 202)) })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(0)
    expect(screen.getByRole('alert')).toBeTruthy()

    submit()
    await advance(0)
    expect(api.posts()).toHaveLength(2)
    expect(screen.getByRole('status').textContent).toMatch(/Processing call #42/)
  })
})

describe('polling and completion', () => {
  it('shows processing, checks every 2 seconds, and opens the review when completed', async () => {
    const api = backend({ statuses: ['PROCESSING', 'PROCESSING', 'COMPLETED'] })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(0)
    expect(screen.getByRole('status').textContent).toMatch(/Processing call #42/)
    expect(api.polls()).toHaveLength(0)

    await advance(2000)
    expect(api.polls()).toHaveLength(1)
    expect(api.polls()[0][0]).toBe('/api/v1/calls/42')
    await advance(2000)
    expect(api.polls()).toHaveLength(2)
    expect(onOpenCall).not.toHaveBeenCalled()

    await advance(2000)
    expect(api.polls()).toHaveLength(3)
    expect(onOpenCall).toHaveBeenCalledTimes(1)
    expect(onOpenCall).toHaveBeenCalledWith(42) // the integer id from the backend
    expect(screen.getByRole('status').textContent).toMatch(/Processing complete/)

    await advance(30000)
    expect(api.polls()).toHaveLength(3) // stopped
  })

  it('a FAILED call stops the polling and shows a failure, not a timeout', async () => {
    const api = backend({ statuses: ['PROCESSING', 'FAILED'] })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(4000)

    const alert = screen.getByRole('alert').textContent
    expect(alert).toMatch(/Processing failed\. Call #42 was uploaded/)
    expect(alert).not.toMatch(/Still processing/)
    expect(onOpenCall).not.toHaveBeenCalled()

    await advance(60000)
    expect(api.polls()).toHaveLength(2) // stopped

    fireEvent.click(screen.getByRole('button', { name: 'View call #42' }))
    expect(onOpenCall).toHaveBeenCalledWith(42) // partial results can still be looked at
  })

  it('still PROCESSING after 60 checks is a timeout, distinct from a backend failure', async () => {
    const api = backend({ statuses: ['PROCESSING'] })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(0)

    await advance(119999)
    expect(api.polls()).toHaveLength(59)
    expect(screen.getByRole('status')).toBeTruthy() // still waiting
    await advance(1)

    const alert = screen.getByRole('alert').textContent
    expect(alert).toMatch(/Still processing after 2 minutes/)
    expect(alert).not.toMatch(/Processing failed/)
    expect(api.polls()).toHaveLength(60)
    expect(onOpenCall).not.toHaveBeenCalled()

    await advance(120000)
    expect(api.polls()).toHaveLength(60) // stopped

    fireEvent.click(screen.getByRole('button', { name: 'View call #42' }))
    expect(onOpenCall).toHaveBeenCalledWith(42)
  })

  it('carries on through a network blip while polling', async () => {
    backend({ statuses: [new TypeError('Failed to fetch'), 503, 'COMPLETED'] })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(6000)
    expect(onOpenCall).toHaveBeenCalledWith(42)
  })

  it('a call that no longer exists stops the polling with an error', async () => {
    const api = backend({ statuses: [404] })
    render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(2000)

    expect(screen.getByRole('alert').querySelector('p').textContent).toBe('Could not check the status of call #42: Call 42 was not found.')
    await advance(60000)
    expect(api.polls()).toHaveLength(1)
  })

  it('unmounting stops the polling and never opens the review', async () => {
    const api = backend({ statuses: ['PROCESSING', 'PROCESSING', 'COMPLETED'] })
    const { unmount } = render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(2000)
    expect(api.polls()).toHaveLength(1)

    unmount()
    await advance(120000)

    expect(api.polls()).toHaveLength(1) // no leaked poll after navigation
    expect(onOpenCall).not.toHaveBeenCalled()
  })

  it('unmounting during the upload cancels it too', async () => {
    let signal
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url, init) => {
      signal = init.signal
      return new Promise(() => {}) // never answers
    }))
    const { unmount } = render(<UploadCall onOpenCall={onOpenCall} />)
    fill()
    submit()
    await advance(0)
    expect(signal.aborted).toBe(false)

    unmount()
    expect(signal.aborted).toBe(true)
  })
})

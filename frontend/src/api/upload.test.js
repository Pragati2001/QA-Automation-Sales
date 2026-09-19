import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getCall, uploadCall, waitForCall } from './client.js'

afterEach(() => vi.unstubAllGlobals())

const json = (body, status = 200) => new Response(JSON.stringify(body), { status })

describe('uploadCall', () => {
  const audio = new File(['fake audio'], 'call.mp3', { type: 'audio/mpeg' })
  const created = { call_id: 42, status: 'PROCESSING' }

  it('posts multipart form data with exactly the backend field names', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(created, 202)))

    const result = await uploadCall({ retailerCode: ' retailer1 ', externalLeadId: '3613790 ', audio })

    expect(result).toEqual({ call_id: 42, status: 'PROCESSING' })
    expect(typeof result.call_id).toBe('number')
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/v1/calls')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect([...init.body.keys()].sort()).toEqual(['audio', 'external_lead_id', 'retailer_code'])
    expect(init.body.get('retailer_code')).toBe('retailer1') // trimmed
    expect(init.body.get('external_lead_id')).toBe('3613790')
    expect(init.body.get('audio').name).toBe('call.mp3')
    expect(init.body.has('call_started_at')).toBe(false) // the backend sets it
  })

  it('does not set a Content-Type, so the browser can add the multipart boundary', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(created, 202)))
    await uploadCall({ retailerCode: 'r', externalLeadId: '1', audio })
    const headers = fetch.mock.calls[0][1].headers
    expect(Object.keys(headers).map((h) => h.toLowerCase())).not.toContain('content-type')
  })

  it("shows the server's message for an unknown retailer (not a 'call not found')", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: "Unknown retailer_code 'nope'" }, 404)))
    await expect(uploadCall({ retailerCode: 'nope', externalLeadId: '1', audio })).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: "Unknown retailer_code 'nope'",
    })
  })

  it('turns FastAPI validation errors into a readable message', async () => {
    const detail = [{ type: 'missing', loc: ['body', 'audio'], msg: 'Field required' }]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail }, 422)))
    await expect(uploadCall({ retailerCode: 'r', externalLeadId: '1', audio })).rejects.toMatchObject({
      status: 422,
      message: 'audio: Field required',
    })
  })

  it('reports an unreachable server', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(uploadCall({ retailerCode: 'r', externalLeadId: '1', audio })).rejects.toMatchObject({ status: 0 })
  })
})

describe('getCall', () => {
  it('fetches the call', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ id: 42, status: 'COMPLETED' })))
    expect(await getCall(42)).toEqual({ id: 42, status: 'COMPLETED' })
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/calls/42')
  })

  it('has a friendly 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'x' }, 404)))
    await expect(getCall(7)).rejects.toMatchObject({ status: 404, message: 'Call 7 was not found.' })
  })
})

describe('waitForCall', () => {
  const statusSequence = (...statuses) => {
    let i = 0
    return vi.fn().mockImplementation(async () => json({ id: 42, status: statuses[Math.min(i++, statuses.length - 1)] }))
  }

  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('polls every 2 seconds and resolves when the call completes', async () => {
    vi.stubGlobal('fetch', statusSequence('PROCESSING', 'PROCESSING', 'COMPLETED'))
    const attempts = []
    const result = waitForCall(42, { onAttempt: (n) => attempts.push(n) })

    expect(fetch).not.toHaveBeenCalled() // the first check is after one interval
    await vi.advanceTimersByTimeAsync(1999)
    expect(fetch).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(fetch).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(fetch).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2000)

    expect(await result).toMatchObject({ outcome: 'completed', call: { status: 'COMPLETED' } })
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(attempts).toEqual([1, 2, 3])
    await vi.advanceTimersByTimeAsync(20000)
    expect(fetch).toHaveBeenCalledTimes(3) // stopped
  })

  it('stops with outcome "failed" when the backend reports FAILED', async () => {
    vi.stubGlobal('fetch', statusSequence('PROCESSING', 'FAILED'))
    const result = waitForCall(42)
    await vi.advanceTimersByTimeAsync(4000)
    expect(await result).toMatchObject({ outcome: 'failed', call: { status: 'FAILED' } })
    await vi.advanceTimersByTimeAsync(20000)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('gives up with outcome "timeout" after 60 checks (2 minutes) and then stops', async () => {
    vi.stubGlobal('fetch', statusSequence('PROCESSING'))
    const result = waitForCall(42)
    await vi.advanceTimersByTimeAsync(119999)
    expect(fetch).toHaveBeenCalledTimes(59)
    await vi.advanceTimersByTimeAsync(1)

    expect(await result).toEqual({ outcome: 'timeout' })
    expect(fetch).toHaveBeenCalledTimes(60)
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetch).toHaveBeenCalledTimes(60)
  })

  it('keeps polling through network errors and server errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn()
        .mockRejectedValueOnce(new TypeError('Failed to fetch'))
        .mockResolvedValueOnce(json({ detail: 'restarting' }, 503))
        .mockResolvedValueOnce(json({ id: 42, status: 'COMPLETED' })),
    )
    const result = waitForCall(42)
    await vi.advanceTimersByTimeAsync(6000)
    expect(await result).toMatchObject({ outcome: 'completed' })
  })

  it('stops immediately on a client error such as the call no longer existing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'gone' }, 404)))
    const result = expect(waitForCall(42)).rejects.toMatchObject({ status: 404 })
    await vi.advanceTimersByTimeAsync(2000)
    await result
    await vi.advanceTimersByTimeAsync(20000)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('aborting while waiting cancels the poll and never calls the server again', async () => {
    vi.stubGlobal('fetch', statusSequence('PROCESSING'))
    const controller = new AbortController()
    const result = expect(waitForCall(42, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    await vi.advanceTimersByTimeAsync(2000)
    expect(fetch).toHaveBeenCalledTimes(1)

    controller.abort()
    await result
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('an abort during the very last request is an abort, not a timeout', async () => {
    // a fetch that behaves like the real one: it rejects with an AbortError when its signal aborts
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        (url, init) =>
          new Promise((resolve, reject) => {
            init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
          }),
      ),
    )
    const controller = new AbortController()
    const result = expect(waitForCall(42, { maxAttempts: 1, signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    })
    await vi.advanceTimersByTimeAsync(2000) // the only check is now in flight
    controller.abort()
    await result
  })

  it('an already-aborted signal rejects without any request', async () => {
    vi.stubGlobal('fetch', statusSequence('COMPLETED'))
    const controller = new AbortController()
    controller.abort()
    await expect(waitForCall(42, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('the interval and attempt limit can be changed', async () => {
    vi.stubGlobal('fetch', statusSequence('PROCESSING'))
    const result = waitForCall(42, { intervalMs: 100, maxAttempts: 3 })
    await vi.advanceTimersByTimeAsync(300)
    expect(await result).toEqual({ outcome: 'timeout' })
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})

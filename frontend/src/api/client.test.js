import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, audioUrl, getReview } from './client.js'

afterEach(() => vi.unstubAllGlobals())

const json = (body, status = 200) => new Response(JSON.stringify(body), { status })

describe('audioUrl', () => {
  it('points at the audio endpoint of the call', () => {
    expect(audioUrl(42)).toBe('/api/v1/calls/42/audio')
  })

  it('escapes the id', () => {
    expect(audioUrl('4/2')).toBe('/api/v1/calls/4%2F2/audio')
  })
})

describe('getReview', () => {
  it('returns the parsed payload', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ call: { id: 1 } })))
    expect(await getReview(1)).toEqual({ call: { id: 1 } })
    expect(fetch).toHaveBeenCalledWith('/api/v1/calls/1/review', expect.objectContaining({ headers: { Accept: 'application/json' } }))
  })

  it('passes the abort signal to fetch', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({})))
    const controller = new AbortController()
    await getReview(1, { signal: controller.signal })
    expect(fetch.mock.calls[0][1].signal).toBe(controller.signal)
  })

  it('turns a 404 into a friendly ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'Call 9 not found' }, 404)))
    await expect(getReview(9)).rejects.toMatchObject({ name: 'ApiError', status: 404, message: 'Call 9 was not found.' })
  })

  it('uses the server detail for other errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'Something specific' }, 400)))
    await expect(getReview(1)).rejects.toMatchObject({ status: 400, message: 'Something specific' })
  })

  it('falls back to a generic message when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>boom</html>', { status: 500 })))
    await expect(getReview(1)).rejects.toMatchObject({ status: 500, message: 'The server returned an error (500).' })
  })

  it('reports an unreachable server', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = await getReview(1).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
    expect(error.message).toMatch(/could not reach the server/i)
  })

  it('lets an abort through untouched so callers can ignore it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')))
    await expect(getReview(1)).rejects.toMatchObject({ name: 'AbortError' })
  })
})

import { describe, expect, it } from 'vitest'
import { formatBytes, formatDateTime, formatTime } from './format.js'

describe('formatTime', () => {
  it.each([
    [0, '0:00'],
    [7.4, '0:07'],
    [21.4, '0:21'],
    [89.2, '1:29'],
    [600, '10:00'],
    [3599.9, '59:59'],
    [3600, '1:00:00'],
    [3725, '1:02:05'],
    [-5, '0:00'],
    [undefined, '0:00'],
    ['12', '0:12'],
  ])('%s -> %s', (seconds, expected) => {
    expect(formatTime(seconds)).toBe(expected)
  })
})

describe('formatBytes', () => {
  it.each([[512, '512 B'], [2048, '2.0 KB'], [32044, '31.3 KB'], [5 * 1024 * 1024, '5.0 MB'], [undefined, '—']])(
    '%s -> %s',
    (bytes, expected) => expect(formatBytes(bytes)).toBe(expected),
  )
})

describe('formatDateTime', () => {
  it('shows a dash for a missing date', () => {
    expect(formatDateTime(null)).toBe('—')
  })

  it('leaves an unparseable value as it is', () => {
    expect(formatDateTime('not a date')).toBe('not a date')
  })

  it('formats a real date', () => {
    expect(formatDateTime('2026-03-10T10:30:00+05:30')).toMatch(/2026/)
  })
})

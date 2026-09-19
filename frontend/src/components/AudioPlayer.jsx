import { useImperativeHandle, useRef, useState } from 'react'
import { formatTime } from '../lib/format.js'

/**
 * The call recording. Exposes seekAndPlay(seconds) through `ref`, so the review page can jump to
 * an evidence timestamp and start playing from there. The browser fetches the audio with Range
 * requests, so seeking does not wait for a full download.
 */
export default function AudioPlayer({ src, ref }) {
  const audioRef = useRef(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  useImperativeHandle(
    ref,
    () => ({
      seekAndPlay(seconds) {
        const audio = audioRef.current
        if (!audio) return
        setError('')
        // Setting currentTime before the metadata has loaded is fine: the browser applies it as
        // the start position once it has.
        audio.currentTime = seconds
        setNote(`Playing from ${formatTime(seconds)}`)
        const playing = audio.play()
        if (playing && typeof playing.catch === 'function') {
          playing.catch(() => setError('The browser blocked playback. Press play on the player.'))
        }
      },
    }),
    [],
  )

  return (
    <div className="audio-player">
      <audio
        ref={audioRef}
        controls
        preload="metadata"
        src={src}
        aria-label="Call recording"
        onError={() => setError('The recording could not be loaded.')}
        onPlaying={() => setError('')}
      />
      <p className="audio-player__note" aria-live="polite">
        {error ? <span className="audio-player__error">{error}</span> : note || 'Click an evidence line to jump to it.'}
      </p>
    </div>
  )
}

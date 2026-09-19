import { useEffect, useState } from 'react'
import LeadReview from './components/LeadReview.jsx'

// /calls/123/review shows call 123; anything else shows a box to enter a call id.
function callIdFromLocation(): string | null {
  const match = window.location.pathname.match(/^\/calls\/(\d+)\/review\/?$/)
  return match ? match[1] : new URLSearchParams(window.location.search).get('call')
}

function App() {
  const [callId, setCallId] = useState<string | null>(callIdFromLocation)
  const [draft, setDraft] = useState('')

  useEffect(() => {
    const onNavigate = () => setCallId(callIdFromLocation())
    window.addEventListener('popstate', onNavigate)
    return () => window.removeEventListener('popstate', onNavigate)
  }, [])

  if (callId) return <LeadReview callId={callId} />

  return (
    <form
      className="open-call"
      onSubmit={(event) => {
        event.preventDefault()
        const id = draft.trim()
        if (!/^\d+$/.test(id)) return
        window.history.pushState({}, '', `/calls/${id}/review`)
        setCallId(id)
      }}
      style={{ maxWidth: '24rem', margin: '4rem auto', padding: '0 1rem' }}
    >
      <h1 style={{ fontSize: '1.25rem' }}>Open a call review</h1>
      <label>
        Call ID{' '}
        <input value={draft} onChange={(event) => setDraft(event.target.value)} inputMode="numeric" autoFocus />
      </label>{' '}
      <button type="submit" disabled={!/^\d+$/.test(draft.trim())}>
        Open
      </button>
    </form>
  )
}

export default App

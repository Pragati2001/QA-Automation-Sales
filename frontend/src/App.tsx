import { useEffect, useState } from 'react'
import LeadReview from './components/LeadReview.jsx'
import UploadCall from './components/UploadCall.jsx'

// /calls/123/review shows call 123; anything else shows the upload page (and a box to open an existing call).
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

  const openCall = (id: string | number) => {
    window.history.pushState({}, '', `/calls/${id}/review`)
    setCallId(String(id))
  }

  if (callId) return <LeadReview callId={callId} />

  return (
    <>
      <UploadCall onOpenCall={openCall} />
      <form
        className="open-call"
        onSubmit={(event) => {
          event.preventDefault()
          const id = draft.trim()
          if (/^\d+$/.test(id)) openCall(id)
        }}
        style={{ maxWidth: '34rem', margin: '0 auto 3rem', padding: '0 1rem' }}
      >
        <label>
          Or open an existing call by ID:{' '}
          <input value={draft} onChange={(event) => setDraft(event.target.value)} inputMode="numeric" />
        </label>{' '}
        <button type="submit" disabled={!/^\d+$/.test(draft.trim())}>
          Open
        </button>
      </form>
    </>
  )
}

export default App

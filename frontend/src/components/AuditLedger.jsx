import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ''
  }
}

export default function AuditLedger({ sessionId }) {
  const [events, setEvents] = useState([])
  const entriesRef = useRef(null)

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false

    async function poll() {
      try {
        const data = await api.auditTrail(sessionId)
        if (!cancelled) setEvents(data.events)
      } catch {
        // audit fetch failures are non-fatal to the UI
      }
    }
    poll()
    const interval = setInterval(poll, 2000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [sessionId])

  // Keep the newest entry in view as events stream in — but only if the user
  // is already near the bottom, so scrolling up to read isn't yanked back down.
  useEffect(() => {
    const el = entriesRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [events])

  return (
    <div className="ledger">
      <p className="ledger-title">Audit trail</p>
      <p className="ledger-sub">
        {sessionId ? `session ${sessionId.slice(0, 8)}` : 'no active session yet'}
      </p>
      <div className="ledger-entries" ref={entriesRef}>
        {events.length === 0 && <div className="ledger-empty">Every agent decision will appear here as it happens.</div>}
        {events.map((e) => (
          <div className="ledger-entry" key={e.id}>
            <span className="ledger-step">{e.step.replaceAll('_', ' ')}</span>
            <span className="ledger-time">{formatTime(e.created_at)}</span>
            <div className="ledger-payload">{JSON.stringify(e.payload, null, 0)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

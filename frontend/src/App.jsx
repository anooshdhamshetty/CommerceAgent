import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import ProposalCard from './components/ProposalCard'
import FallbackCard from './components/FallbackCard'
import RelaxationCard from './components/RelaxationCard'
import ReceiptCard from './components/ReceiptCard'
import UpsellCard from './components/UpsellCard'
import AuditLedger from './components/AuditLedger'

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([
    { role: 'agent', text: "Tell me what you'd like to buy — e.g. \"3 lavender candles under ₹1500\"." },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [razorpayKeyId, setRazorpayKeyId] = useState('')
  const scrollRef = useRef(null)

  useEffect(() => {
    api.config().then((c) => setRazorpayKeyId(c.razorpay_key_id)).catch(() => {})
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Shared send path: used by the input bar AND by relaxation-option buttons,
  // which re-run the pipeline with a rephrased query as a brand-new request.
  // fromRelaxation=true tags button-driven runs so the backend can cap how many
  // times the shopper adjusts before it stops offering more options.
  async function sendMessage(text, fromRelaxation = false) {
    if (!text || loading) return
    setMessages((m) => [...m, { role: 'user', text }])
    setLoading(true)
    try {
      const result = await api.chat(sessionId, text, fromRelaxation)
      setSessionId(result.session_id)

      if (result.status === 'proposal') {
        setMessages((m) => [...m, { role: 'agent', proposal: result }])
      } else if (result.status === 'relax') {
        // Adjust loop exhausted (or no options left): show a final message with
        // no buttons instead of another relaxation card.
        if (result.exhausted || !(result.relaxations && result.relaxations.length)) {
          const text = result.message ||
            "I couldn't find a match. Try a new search with different requirements."
          setMessages((m) => [...m, { role: 'agent', text }])
        } else {
          setMessages((m) => [...m, { role: 'agent', relax: result }])
        }
      } else if (result.status === 'fallback') {
        setMessages((m) => [...m, { role: 'agent', fallback: result }])
      } else if (result.status === 'error') {
        setMessages((m) => [...m, { role: 'agent', text: result.message }])
      } else {
        setMessages((m) => [...m, { role: 'agent', text: 'Unexpected response — check the backend logs.' }])
      }
    } catch (e) {
      setMessages((m) => [...m, { role: 'agent', text: `Error: ${e.message}` }])
    } finally {
      setLoading(false)
    }
  }

  function handleSend() {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    sendMessage(text)
  }

  function handleSettled(receipt) {
    setMessages((m) => [...m, { role: 'agent', receipt }])
    if (receipt.upsell?.suggest) {
      setMessages((m) => [...m, { role: 'agent', upsell: receipt.upsell }])
    }
  }

  function handleUpsellSettled(receipt) {
    setMessages((m) => [...m, { role: 'agent', receipt }])
  }

  return (
    <div className="app-shell">
      <div className="header">
        <h1>Agentic checkout — buildathon demo</h1>
        <p>Query → fetch → reasoning → deterministic gate → order → payment, all logged.</p>
        <span className="badge">Razorpay test mode</span>
      </div>

      <div className="chat-panel">
        <div className="chat-scroll">
        {messages.map((m, i) => {
          const isLast = i === messages.length - 1
          if (m.proposal) return <ProposalCard key={i} proposal={m.proposal} sessionId={sessionId} razorpayKeyId={razorpayKeyId} active={isLast} onSettled={handleSettled} />
          if (m.relax) return <RelaxationCard key={i} relax={m.relax} disabled={loading} active={isLast} onChoose={(q) => sendMessage(q, true)} />
          if (m.fallback) return <FallbackCard key={i} fallback={m.fallback} />
          if (m.receipt) return <ReceiptCard key={i} receipt={m.receipt} />
          if (m.upsell) return <UpsellCard key={i} upsell={m.upsell} sessionId={sessionId} razorpayKeyId={razorpayKeyId} active={isLast} onDone={handleUpsellSettled} />
          return <div key={i} className={`msg ${m.role}`}>{m.text}</div>
        })}
        <div ref={scrollRef} />
        </div>

        <div className="input-bar">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="What would you like to buy?"
            disabled={loading}
          />
          <button className="btn btn-primary" onClick={handleSend} disabled={loading}>
            {loading ? '…' : 'Send'}
          </button>
        </div>
      </div>

      <div className="ledger-panel">
        <AuditLedger sessionId={sessionId} />
      </div>
    </div>
  )
}

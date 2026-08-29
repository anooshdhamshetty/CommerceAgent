import { useState, useRef } from 'react'
import { api } from '../api'

export default function UpsellCard({ upsell, sessionId, razorpayKeyId, onDone, active = true }) {
  const [stage, setStage] = useState('offer') // offer -> processing -> done / declined / cancelled
  const [error, setError] = useState(null)
  const rzpRef = useRef(null)

  async function accept() {
    // Ignore clicks on a stale card or while an accept is already in flight.
    if (!active || stage === 'processing') return
    setError(null)
    setStage('processing')
    try {
      const res = await api.upsellRespond(sessionId, upsell.sku, true)
      openCheckout(res)
    } catch (e) {
      setError(e.message)
      setStage('offer')
    }
  }

  function decline() {
    if (!active || stage === 'processing') return
    api.upsellRespond(sessionId, upsell.sku, false).catch(() => {})
    setStage('declined')
  }

  function openCheckout(order) {
    const rzp = new window.Razorpay({
      key: razorpayKeyId,
      amount: Math.round(order.amount * 100),
      currency: 'INR',
      name: 'Wick & Wax (test mode)',
      description: `Add-on: ${order.sku}`,
      order_id: order.razorpay_order_id,
      handler: async function (response) {
        try {
          const receipt = await api.verifyPayment({
            session_id: sessionId,
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
          })
          rzpRef.current = null
          setStage('done')
          onDone(receipt)
        } catch (e) {
          setError(e.message)
          setStage('offer')
        }
      },
      modal: { ondismiss: function () { handleUpsellCancelled(order.razorpay_order_id) } },
      theme: { color: '#0F6E56' },
    })
    rzpRef.current = rzp
    rzp.open()
  }

  function cancelNow() {
    if (rzpRef.current) rzpRef.current.close()
    else handleUpsellCancelled(null)
  }

  function handleUpsellCancelled(razorpayOrderId) {
    rzpRef.current = null
    setError(null)
    setStage('cancelled')
    api.cancelPayment(sessionId, razorpayOrderId).catch(() => {})
  }

  if (stage === 'declined') {
    return <div className="msg agent">No problem — noted for next time.</div>
  }
  if (stage === 'done') return null

  return (
    <div className="card">
      <p className="card-title">Since you're buying that… <span className="chip">upsell agent</span></p>
      <p style={{ fontSize: 14, color: 'var(--ink-muted)', margin: '0 0 10px' }}>{upsell.reason}</p>
      <div className="line-item"><span className="label">{upsell.name}</span><span>₹{upsell.price}</span></div>
      {error && <div className="notice error">{error}</div>}
      {stage === 'cancelled' && <div className="notice cancelled">Add-on payment cancelled — nothing was charged.</div>}
      <div className="btn-row">
        <button className="btn btn-primary" onClick={accept} disabled={stage === 'processing' || !active}>
          {stage === 'processing' ? 'Processing…' : stage === 'cancelled' ? 'Try again' : `Add for ₹${upsell.price}`}
        </button>
        {stage === 'processing' && (
          <button className="btn btn-secondary" onClick={cancelNow}>Cancel payment</button>
        )}
        {stage !== 'processing' && active && (
          <button className="btn btn-secondary" onClick={decline}>No thanks</button>
        )}
      </div>
    </div>
  )
}

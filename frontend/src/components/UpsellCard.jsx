import { useState, useRef } from 'react'
import { api } from '../api'
import OtpPopup from './OtpPopup'

export default function UpsellCard({ upsell, sessionId, razorpayKeyId, onDone, active = true }) {
  const [stage, setStage] = useState('offer') // offer -> processing -> otp_pending -> done / declined / cancelled
  const [error, setError] = useState(null)
  const [gateToken, setGateToken] = useState(null)
  const [otpCode, setOtpCode] = useState('')
  const [demoOtp, setDemoOtp] = useState(null)
  const [resending, setResending] = useState(false)
  const rzpRef = useRef(null)

  async function accept() {
    // Ignore clicks on a stale card or while an accept is already in flight.
    if (!active || stage === 'processing') return
    setError(null)
    setStage('processing')
    try {
      const res = await api.upsellRespond(sessionId, upsell.sku, true)
      if (res.requires_otp) {
        // Upsells priced above the auto-approve limit hit the same gate as a
        // main order now, instead of failing outright.
        setGateToken(res.gate_token)
        setDemoOtp(res.otp_code || null)
        setStage('otp_pending')
        return
      }
      openCheckout(res)
    } catch (e) {
      setError(e.message)
      setStage('offer')
    }
  }

  async function verifyOtp() {
    setError(null)
    try {
      const res = await api.verifyOtp(sessionId, gateToken, otpCode)
      if (!res.approved) {
        setError(res.reason || 'Incorrect code.')
        return
      }
      setDemoOtp(null)
      setStage('processing')
      openCheckout(res)
    } catch (e) {
      setError(e.message)
    }
  }

  async function resendOtp() {
    setError(null)
    setResending(true)
    try {
      const result = await api.resendOtp(sessionId, gateToken)
      if (!result.success) {
        setError(result.reason || 'Could not resend the code.')
        if (/expired/i.test(result.reason || '')) {
          setStage('offer')
          setGateToken(null)
        }
        return
      }
      setDemoOtp(result.otp_code || null)
      setOtpCode('')
    } catch (e) {
      setError(e.message)
    } finally {
      setResending(false)
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
      {stage === 'otp_pending' && <OtpPopup code={demoOtp} onClose={() => setDemoOtp(null)} />}
      <p className="card-title">Since you're buying that… <span className="chip">upsell agent</span></p>
      <p style={{ fontSize: 14, color: 'var(--ink-muted)', margin: '0 0 10px' }}>{upsell.reason}</p>
      <div className="line-item"><span className="label">{upsell.name}</span><span>₹{upsell.price}</span></div>
      {error && <div className="notice error">{error}</div>}
      {stage === 'cancelled' && <div className="notice cancelled">Add-on payment cancelled — nothing was charged.</div>}

      {stage !== 'otp_pending' && (
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
      )}

      {stage === 'otp_pending' && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={otpCode}
              onChange={(e) => setOtpCode(e.target.value)}
              placeholder="6-digit code"
              maxLength={6}
              style={{ flex: 1, padding: '10px 12px', borderRadius: 8, border: '1px solid var(--line)', fontFamily: 'var(--font-mono)' }}
            />
            <button className="btn btn-primary" onClick={verifyOtp}>Verify</button>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 8 }}>
            <p className="footer-note" style={{ margin: 0 }}>
              This add-on is above the auto-approve limit, so a one-time code is required.
              {demoOtp ? ' Use the code shown in the popup above.' : ' Check the backend server console for the code.'}
            </p>
            <button
              type="button"
              onClick={resendOtp}
              disabled={resending}
              style={{ background: 'none', border: 'none', color: 'var(--teal)', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap', padding: 0, marginLeft: 12 }}
            >
              {resending ? 'Sending…' : 'Code expired? Resend'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
import { useState, useRef } from 'react'
import { api } from '../api'

export default function ProposalCard({ proposal, sessionId, razorpayKeyId, onSettled }) {
  const [stage, setStage] = useState('review') // review -> gating -> otp_pending -> paying -> done / cancelled
  const [error, setError] = useState(null)
  const [gateToken, setGateToken] = useState(null)
  const [otpCode, setOtpCode] = useState('')
  const rzpRef = useRef(null)

  async function handleConfirmOrder() {
    setError(null)
    setStage('gating')
    try {
      const result = await api.confirmOrder(sessionId)
      if (!result.approved) {
        setError(result.reason || 'Order was not approved by the gate.')
        setStage('review')
        return
      }
      if (result.requires_otp) {
        setGateToken(result.gate_token)
        setStage('otp_pending')
        return
      }
      openRazorpayCheckout(result)
    } catch (e) {
      setError(e.message)
      setStage('review')
    }
  }

  async function handleVerifyOtp() {
    setError(null)
    try {
      const result = await api.verifyOtp(sessionId, gateToken, otpCode)
      if (!result.approved) {
        setError(result.reason || 'Incorrect code.')
        return
      }
      openRazorpayCheckout(result)
    } catch (e) {
      setError(e.message)
    }
  }

  function openRazorpayCheckout(order) {
    if (!razorpayKeyId) {
      setError('RAZORPAY_KEY_ID is not configured on the backend — add your test key to backend/.env.')
      setStage('review')
      return
    }
    setStage('paying')
    const rzp = new window.Razorpay({
      key: razorpayKeyId,
      amount: Math.round(order.amount * 100),
      currency: 'INR',
      name: 'Wick & Wax (test mode)',
      description: `${order.quantity} x ${order.sku}`,
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
          onSettled(receipt)
        } catch (e) {
          setError(e.message)
          setStage('review')
        }
      },
      modal: { ondismiss: function () { handlePaymentCancelled(order.razorpay_order_id) } },
      theme: { color: '#0F6E56' },
    })
    rzpRef.current = rzp
    rzp.on('payment.failed', function () {
      setError('Payment failed or was declined.')
      setStage('review')
    })
    rzp.open()
  }

  // Explicit "Cancel payment" button — closes the Razorpay modal, which then
  // fires ondismiss below and records the cancellation.
  function cancelNow() {
    if (rzpRef.current) rzpRef.current.close()
    else handlePaymentCancelled(null)
  }

  function handlePaymentCancelled(razorpayOrderId) {
    rzpRef.current = null
    setError(null)
    setStage('cancelled')
    api.cancelPayment(sessionId, razorpayOrderId).catch(() => {})
  }

    if (stage === 'declined') {
    return <div className="msg agent">No problem — not proceeding with that one.</div>
  }

  return (
    <div className="card">
      <p className="card-title">
        {proposal.exact_match === false ? 'Closest match found' : 'Proposed order'}
      </p>
      {proposal.exact_match === false && (
        <div className="notice warn">
          Not an exact match — {proposal.note}
        </div>
      )}
      <div className="line-item"><span className="label">Item</span><span>{proposal.name}</span></div>
      <div className="line-item"><span className="label">Quantity</span><span>{proposal.quantity}</span></div>
      <div className="line-item"><span className="label">Unit price</span><span>₹{proposal.unit_price}</span></div>
      <div className="line-item"><span className="label">Delivery</span><span>{proposal.delivery_days} day(s)</span></div>
      <div className="total-row"><span>Total</span><span>₹{proposal.total_amount}</span></div>

      {error && <div className="notice error">{error}</div>}
      {stage === 'cancelled' && <div className="notice cancelled">Payment cancelled — nothing was charged.</div>}

      {stage !== 'otp_pending' && (
        <div className="btn-row">
          <button className="btn btn-primary" onClick={handleConfirmOrder} disabled={stage !== 'review' && stage !== 'cancelled'}>
            {stage === 'review' && (proposal.exact_match === false ? 'Confirm substitute' : 'Confirm order')}
            {stage === 'gating' && 'Checking policy…'}
            {stage === 'paying' && 'Waiting for payment…'}
            {stage === 'cancelled' && 'Try again'}
            {stage === 'done' && 'Paid'}
          </button>
          {stage === 'paying' && (
            <button className="btn btn-secondary" onClick={cancelNow}>Cancel payment</button>
          )}
          {(stage === 'review' || stage === 'cancelled') && (
            <button className="btn btn-secondary" onClick={() => setStage('declined')}>No thanks</button>
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
            <button className="btn btn-primary" onClick={handleVerifyOtp}>Verify</button>
          </div>
          <p className="footer-note">
            This order is above the auto-approve limit, so a one-time code is required.
            For this demo, check the <strong>backend server console</strong> for the code.
          </p>
        </div>
      )}
    </div>
  )

}

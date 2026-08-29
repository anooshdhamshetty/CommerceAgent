const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function post(path, body) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Request failed')
  return data
}

async function get(path) {
  const res = await fetch(`${API_URL}${path}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Request failed')
  return data
}

export const api = {
  chat: (session_id, message, from_relaxation = false) =>
    post('/api/chat', { session_id, message, from_relaxation }),
  confirmOrder: (session_id) => post('/api/confirm-order', { session_id }),
  verifyOtp: (session_id, gate_token, code) => post('/api/verify-otp', { session_id, gate_token, code }),
  resendOtp: (session_id, gate_token) => post('/api/resend-otp', { session_id, gate_token }),
  verifyPayment: (payload) => post('/api/verify-payment', payload),
  cancelPayment: (session_id, razorpay_order_id) => post('/api/cancel-payment', { session_id, razorpay_order_id }),
  upsellRespond: (session_id, sku, accepted) => post('/api/upsell-respond', { session_id, sku, accepted }),
  auditTrail: (session_id) => get(`/api/audit/${session_id}`),
  config: () => get('/api/config'),
}
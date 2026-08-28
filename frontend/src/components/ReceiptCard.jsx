export default function ReceiptCard({ receipt }) {
  return (
    <div className="card">
      <p className="card-title">Payment captured <span className="chip">test mode</span></p>
      <div className="line-item"><span className="label">SKU</span><span>{receipt.sku}</span></div>
      <div className="line-item"><span className="label">Quantity</span><span>{receipt.quantity}</span></div>
      <div className="line-item"><span className="label">Razorpay order</span><span>{receipt.razorpay_order_id}</span></div>
      <div className="line-item"><span className="label">Razorpay payment</span><span>{receipt.razorpay_payment_id}</span></div>
      <div className="total-row"><span>Paid</span><span>₹{receipt.amount}</span></div>
    </div>
  )
}

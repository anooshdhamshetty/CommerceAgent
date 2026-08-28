export default function FallbackCard({ fallback }) {
  return (
    <div className="card">
      <p className="card-title">Couldn't complete that exactly</p>
      <p style={{ fontSize: 14, color: 'var(--ink-muted)', margin: '0 0 6px' }}>
        {fallback.message}
      </p>
      {fallback.alternatives?.length > 0 ? (
        <div className="alt-list">
          {fallback.alternatives.map((p) => (
            <div className="alt-item" key={p.sku}>
              <span>{p.name}</span>
              <span>₹{p.price} · {p.stock} in stock</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="notice warn">No in-stock alternatives found either — try a different request.</div>
      )}
    </div>
  )
}

import { useState } from 'react'

export default function RelaxationCard({ relax, onChoose, disabled, active = true }) {
  const options = relax.relaxations || []
  // Once the shopper picks an option we lock the whole card so the same
  // adjustment can't be fired twice. `active` is false on any card that's no
  // longer the newest message, which greys out stale suggestions too.
  const [chosen, setChosen] = useState(null)
  const locked = disabled || chosen !== null || active === false

  function choose(idx, query) {
    if (locked) return
    setChosen(idx)
    onChoose(query)
  }

  return (
    <div className="card">
      <p className="card-title">No exact match — pick an adjustment</p>
      {relax.note && <div className="notice warn">{relax.note}</div>}
      {typeof relax.match_score === 'number' && (
        <div className="line-item"><span className="label">Best match score</span><span>{relax.match_score}%</span></div>
      )}
      {relax.message && <p className="footer-note">{relax.message}</p>}
      <div className="btn-row" style={{ flexWrap: 'wrap' }}>
        {options.map((r, idx) => (
          <button
            key={idx}
            className="btn btn-secondary"
            disabled={locked}
            onClick={() => choose(idx, r.query)}
          >
            {chosen === idx ? `✓ ${r.label}` : r.label}
          </button>
        ))}
      </div>
      {chosen !== null && <p className="footer-note">Adjusting your search…</p>}
      {chosen === null && active === false && (
        <p className="footer-note">This suggestion is no longer active — send a new message to continue.</p>
      )}
    </div>
  )
}

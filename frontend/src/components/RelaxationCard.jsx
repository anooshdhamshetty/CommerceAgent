export default function RelaxationCard({ relax, onChoose, disabled }) {
  const options = relax.relaxations || []
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
            disabled={disabled}
            onClick={() => onChoose(r.query)}
          >
            {r.label}
          </button>
        ))}
      </div>
    </div>
  )
}

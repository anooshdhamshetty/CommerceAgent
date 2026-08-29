// Demo-only convenience: shows the OTP code as a popup at the top of the
// page instead of the user having to go read the backend console. Only
// ever renders when the backend actually sent a code (i.e. SHOW_OTP_IN_RESPONSE
// is on) — if that flag is off in main.py, `code` will be undefined and
// nothing here renders, so there's nothing to change on the frontend when
// you turn it off for a real deployment.
export default function OtpPopup({ code, onClose }) {
  if (!code) return null

  return (
    <div
      role="alert"
      style={{
        position: 'fixed',
        top: 16,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 1000,
        background: 'var(--ink)',
        color: '#fff',
        borderRadius: 12,
        padding: '12px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
        fontFamily: 'var(--font-body)',
      }}
    >
      <span style={{ fontSize: 13, color: '#D8D5CC' }}>
        Demo OTP <span style={{ opacity: 0.7 }}>(sent via SMS/email in production)</span>
      </span>
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 18,
          letterSpacing: 3,
          background: 'rgba(255,255,255,0.12)',
          borderRadius: 6,
          padding: '4px 10px',
        }}
      >
        {code}
      </span>
      <button
        onClick={onClose}
        aria-label="Dismiss"
        style={{
          background: 'transparent',
          border: 'none',
          color: '#D8D5CC',
          cursor: 'pointer',
          fontSize: 16,
          lineHeight: 1,
          padding: 4,
        }}
      >
        ✕
      </button>
    </div>
  )
}
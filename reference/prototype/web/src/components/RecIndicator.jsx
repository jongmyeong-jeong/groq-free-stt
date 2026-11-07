import './RecIndicator.css'

export default function RecIndicator({ pulsing = true }) {
  return (
    <div className="rec-chip">
      <div className="rec-chip__dot-wrapper">
        {pulsing && (
          <>
            <span className="rec-chip__ripple rec-chip__ripple--1" />
            <span className="rec-chip__ripple rec-chip__ripple--2" />
          </>
        )}
        <span
          className={
            'rec-chip__dot' + (pulsing ? ' rec-chip__dot--pulsing' : '')
          }
        />
      </div>
      <span className="rec-chip__label">REC</span>
    </div>
  )
}

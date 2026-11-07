import './StopButton.css'

export default function StopButton({ onClick }) {
  return (
    <button
      type="button"
      className="stop-button"
      onClick={onClick}
      aria-label="정지"
    >
      <span className="stop-button__glow" aria-hidden="true" />
      <span className="stop-button__icon" aria-hidden="true" />
    </button>
  )
}

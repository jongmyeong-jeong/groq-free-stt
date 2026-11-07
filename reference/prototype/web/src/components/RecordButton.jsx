import { Mic } from 'lucide-react'
import './RecordButton.css'

export default function RecordButton({ onClick }) {
  return (
    <button
      type="button"
      className="record-button"
      onClick={onClick}
      aria-label="녹음 시작"
    >
      <span className="record-button__glow" aria-hidden="true" />
      <span className="record-button__inner">
        <Mic size={40} strokeWidth={1.5} />
      </span>
    </button>
  )
}

import AmbientBackground from './AmbientBackground'
import RecordButton from './RecordButton'
import './IdleScreen.css'

export default function IdleScreen({ onStart }) {
  return (
    <div className="idle-screen">
      <AmbientBackground />
      <div className="idle-screen__content">
        <RecordButton onClick={onStart} />
      </div>
    </div>
  )
}

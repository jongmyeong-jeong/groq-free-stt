import './TranscribingView.css'

export default function TranscribingView() {
  return (
    <div className="transcribing-view">
      <div className="transcribing-view__spinner" />
      <div className="transcribing-view__text">
        <span className="transcribing-view__title">전사 마무리 중</span>
        <span className="transcribing-view__subtitle">잠시만 기다려 주세요</span>
      </div>
    </div>
  )
}

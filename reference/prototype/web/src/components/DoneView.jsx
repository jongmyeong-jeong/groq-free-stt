import { ArrowLeft, Check, Download } from 'lucide-react'
import './DoneView.css'

export default function DoneView({ noteId, partialFailure, onDownload, onGoHome }) {
  const canDownload = noteId !== null

  return (
    <div className="done-view">
      <div className="done-view__header">
        <div className="done-view__icon">
          <Check size={32} strokeWidth={2.5} />
        </div>
        <span className="done-view__title">전사 완료</span>
        {partialFailure && (
          <span className="done-view__notice">
            일부 구간 전사에 실패했어요 — 파일에 표시되어 있습니다
          </span>
        )}
      </div>

      <div className="done-view__actions">
        <button
          type="button"
          className="done-view__btn done-view__btn--primary"
          onClick={canDownload ? onDownload : undefined}
          disabled={!canDownload}
        >
          <Download size={18} strokeWidth={1.75} />
          파일로 내려받기
        </button>
        <button
          type="button"
          className="done-view__btn done-view__btn--ghost"
          onClick={onGoHome}
        >
          <ArrowLeft size={18} strokeWidth={1.75} />
          시작 화면으로
        </button>
      </div>
    </div>
  )
}

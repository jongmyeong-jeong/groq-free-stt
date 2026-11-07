import { useEffect, useRef } from 'react'
import './ErrorModal.css'

const okButton = (onClose) => [
  <button
    key="ok"
    type="button"
    className="error-modal__btn error-modal__btn--secondary"
    onClick={(e) => { e.currentTarget.blur(); onClose() }}
    autoFocus
  >
    확인
  </button>,
]

const CONTENT = {
  transcription_failed: {
    title: '전사에 실패했어요',
    body: '음성 파일은 저장되어 있습니다. 잠시 후 다시 시도해 주세요.',
    buttons: okButton,
  },
  transport_error: {
    title: '저장 중 오류 발생',
    body: '네트워크 문제로 저장에 실패했습니다. 다시 시도해 주세요.',
    buttons: okButton,
  },
  api_key_missing: {
    title: 'GROQ_API_KEY 미설정',
    body: '환경변수 GROQ_API_KEY를 설정한 뒤 서버를 재시작하세요.',
    buttons: okButton,
  },
  // Mic acquisition errors (previously raw alert())
  mic_denied: {
    title: '마이크 권한이 거부되었습니다',
    body: '브라우저 주소창에서 마이크 권한을 허용한 뒤 다시 시도하세요.',
    buttons: okButton,
  },
  mic_not_found: {
    title: '마이크 장치를 찾을 수 없습니다',
    body: '연결된 마이크가 있는지 확인하고 다시 시도하세요.',
    buttons: okButton,
  },
  mic_https_required: {
    title: '보안 연결이 필요합니다',
    body: 'HTTPS 또는 localhost 환경에서만 마이크를 사용할 수 있습니다.',
    buttons: okButton,
  },
  mic_error: {
    title: '마이크 오류',
    body: '마이크에 접근할 수 없습니다. 잠시 후 다시 시도하세요.',
    buttons: okButton,
  },
  session_create_failed: {
    title: '녹음 세션 생성 실패',
    body: '서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.',
    buttons: okButton,
  },
  browser_unsupported_recorder: {
    title: '지원되지 않는 브라우저',
    body: '이 브라우저는 webm/opus 녹음을 지원하지 않습니다. 최신 Chrome 또는 Edge를 사용하세요.',
    buttons: okButton,
  },
  recording_too_short: {
    title: '녹음이 너무 짧습니다',
    body: '1초 이상 녹음해 주세요.',
    buttons: okButton,
  },
  download_failed: {
    title: '다운로드 실패',
    body: '전사 파일을 내려받지 못했습니다. 잠시 후 다시 시도하세요.',
    buttons: okButton,
  },
}

function getContent(errorType) {
  if (CONTENT[errorType]) return CONTENT[errorType]
  return {
    title: '전사 오류',
    body: `기술적 문제가 발생했습니다. (오류 코드: ${errorType})`,
    buttons: (onClose, onStopRecording) => [
      <button
        key="stop"
        type="button"
        className="error-modal__btn error-modal__btn--primary"
        onClick={onStopRecording}
        autoFocus
      >
        녹음 종료
      </button>,
      <button
        key="continue"
        type="button"
        className="error-modal__btn error-modal__btn--secondary"
        onClick={onClose}
      >
        계속
      </button>,
    ],
  }
}

/**
 * ErrorModal — stateless presentational modal.
 *
 * Props:
 *   open: boolean
 *   errorType: string — key in CONTENT, or any string (uses fallback)
 *   description?: string — overrides the default body for this errorType
 *   onClose: () => void
 *   onStopRecording?: () => void
 */
export default function ErrorModal({ open, errorType, description, onClose, onStopRecording }) {
  const cardRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => {
      const btn = cardRef.current?.querySelector('button[autofocus], button')
      btn?.focus()
    }, 10)
    return () => clearTimeout(timer)
  }, [open])

  if (!open) return null

  const content = getContent(errorType)
  const handleStopRecording = onStopRecording ?? onClose
  const body = description ?? content.body

  return (
    <div
      className="error-modal__backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="error-modal-title"
    >
      <div className="error-modal__card" ref={cardRef}>
        <p id="error-modal-title" className="error-modal__title">{content.title}</p>
        <p className="error-modal__body">{body}</p>
        <div className="error-modal__actions">
          {content.buttons(onClose, handleStopRecording)}
        </div>
      </div>
    </div>
  )
}

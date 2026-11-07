import { useEffect, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowLeft, Check, FileText, LoaderCircle, Pause, Play, Square } from 'lucide-react'
import './GroqFreeSTTStatusPrototype.css'

// Mock states for the interview: no microphone, file upload, or API request is used.

function elapsedTime(seconds) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainingSeconds = seconds % 60
  return [hours, minutes, remainingSeconds].map((part) => String(part).padStart(2, '0')).join(':')
}

export function RecordingStatusPrototype({ source, preview = false, onStop }) {
  const [seconds, setSeconds] = useState(preview ? 756 : 0)
  const [paused, setPaused] = useState(false)

  // The proposed elapsed time includes pauses, matching the agreed transcript timeline.
  useEffect(() => {
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <main className="stt-status-screen" aria-label="녹음 중 화면 초안">
      <div className="stt-recording">
        <div className={`stt-recording__state${paused ? ' is-paused' : ''}`}>
          <span aria-hidden="true" />
          {paused ? '일시 정지' : '녹음 중'}
        </div>
        <div className="stt-recording__source">{source === 'mic' ? '마이크' : '시스템 소리'}</div>
        <div className="stt-recording__time" aria-label={`경과 시간 ${elapsedTime(seconds)}`}>{elapsedTime(seconds)}</div>
        <div className={`stt-recording__meter${paused ? ' is-paused' : ''}`} aria-hidden="true">
          {[12, 22, 32, 19, 40, 28, 16, 35, 23, 44, 30, 17, 37, 25, 13, 33, 21, 38, 18, 29, 14].map((height, index) => (
            <i key={index} style={{ height }} />
          ))}
        </div>
        <div className="stt-status-actions">
          <button className="stt-status-button stt-status-button--secondary" onClick={() => setPaused((value) => !value)} type="button">
            {paused ? <Play size={16} /> : <Pause size={16} />}
            {paused ? '계속' : '일시 정지'}
          </button>
          <button className="stt-status-button stt-status-button--primary" onClick={onStop} type="button">
            <Square size={14} fill="currentColor" /> 녹음 종료
          </button>
        </div>
      </div>
    </main>
  )
}

export function TranscriptionStatusPrototype({ sourceLabel, initialState = 'active', onHome, onViewHome }) {
  const [state, setState] = useState(initialState)
  const [resumeState, setResumeState] = useState(initialState === 'limit' ? 'limit' : 'active')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (state !== 'pausing') return undefined
    const timer = window.setTimeout(() => setState('paused'), 900)
    return () => window.clearTimeout(timer)
  }, [state])

  const isActive = state === 'active'
  const isLimit = state === 'limit'
  const isPausing = state === 'pausing'
  const isPaused = state === 'paused'
  const isComplete = state === 'completed'
  const title = isActive ? '전사 중' : isLimit ? '한도 대기' : isPausing ? '일시 정지 중' : isPaused ? '일시 정지' : '전사 완료'
  const message = isLimit
    ? '다음 자동 재시도까지 기다립니다.'
    : isPausing ? '현재 구간을 마친 뒤 일시 정지합니다.' : isPaused ? '완료한 구간은 보관됩니다.' : ''

  const onPause = () => {
    setResumeState(isLimit ? 'limit' : 'active')
    setState(isLimit ? 'paused' : 'pausing')
  }

  return (
    <main className="stt-status-screen" aria-label="전사 중 화면 초안">
      {!isComplete && <button className="stt-transcription__back" onClick={onViewHome} type="button"><ArrowLeft size={17} /> 뒤로 가기</button>}
      <div className="stt-transcription" role="status" aria-live="polite">
        <div className={`stt-transcription__icon${isActive || isPausing ? ' is-spinning' : ''}`}>
          {isActive || isPausing ? <LoaderCircle size={26} /> : isComplete ? <Check size={23} /> : <Pause size={22} />}
        </div>
        <div className="stt-transcription__title">{title}</div>
        <div className="stt-transcription__source">{sourceLabel}</div>
        {!isComplete && <>
          <div className="stt-transcription__progress-label"><span>3 / 8 구간</span><span>38%</span></div>
          <div className="stt-transcription__progress" role="progressbar" aria-label="전사 진행률 예시" aria-valuenow={38} aria-valuemin={0} aria-valuemax={100}><span /></div>
        </>}
        {message && <p className="stt-transcription__message">{message}</p>}
        {(isActive || isLimit || isPausing || isPaused) && <div className="stt-status-actions stt-transcription__actions">
          {isPaused
            ? <button className="stt-status-button stt-status-button--secondary" onClick={() => setState(resumeState)} type="button"><Play size={15} /> 계속</button>
            : <button className="stt-status-button stt-status-button--secondary" onClick={onPause} disabled={isPausing} type="button"><Pause size={15} /> {isPausing ? '일시 정지 중' : '일시 정지'}</button>}
          <Dialog.Root>
            <Dialog.Trigger asChild><button className="stt-status-button stt-status-button--primary" type="button"><Square size={14} /> 정지</button></Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="stt-dialog-overlay" />
              <Dialog.Content className="stt-dialog">
                <Dialog.Title className="stt-dialog__title">전사를 정지할까요?</Dialog.Title>
                <Dialog.Description className="stt-dialog__description">진행 중인 전사는 삭제되고, 원본 음성은 보관됩니다.</Dialog.Description>
                <div className="stt-status-actions stt-dialog__actions">
                  <Dialog.Close asChild><button className="stt-status-button stt-status-button--secondary" type="button">취소</button></Dialog.Close>
                  <button className="stt-status-button stt-status-button--primary" onClick={onHome} type="button">정지</button>
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>}
        {isComplete && <div className="stt-transcription__downloads">
          <button className="stt-status-button stt-status-button--secondary" onClick={() => setNotice('다운로드는 초안에서 연결되지 않았습니다.')} type="button"><FileText size={15} /> MD</button>
          <button className="stt-status-button stt-status-button--secondary" onClick={() => setNotice('다운로드는 초안에서 연결되지 않았습니다.')} type="button"><FileText size={15} /> TXT</button>
        </div>}
        {notice && <p className="stt-transcription__message">{notice}</p>}
        {isComplete && <button className="stt-status-button stt-status-button--secondary stt-transcription__button" onClick={onHome} type="button"><ArrowLeft size={15} /> 뒤로 가기</button>}
      </div>
    </main>
  )
}

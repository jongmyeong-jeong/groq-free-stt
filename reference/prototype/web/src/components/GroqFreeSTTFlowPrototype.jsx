import { useEffect, useRef, useState } from 'react'
import * as Tabs from '@radix-ui/react-tabs'
import { useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, AudioLines, Download, FileAudio2, Mic2, Pencil, RotateCcw } from 'lucide-react'
import { RecordingStatusPrototype, TranscriptionStatusPrototype } from './GroqFreeSTTStatusPrototype'
import './GroqFreeSTTFlowPrototype.css'

// Three simple main-screen layouts, switchable at the existing / route with ?variant=A|B|C.
// All states are simulated; the prototype never records, uploads, or sends a request.

const VARIANTS = [
  { key: 'A', name: 'Three source cards' },
  { key: 'B', name: 'Simple source list' },
  { key: 'C', name: 'Radix source tabs' },
]

const SOURCES = [
  {
    id: 'system',
    label: '시스템 소리',
    description: 'Mac에서 재생되는 소리를 녹음합니다.',
    icon: AudioLines,
  },
  {
    id: 'mic',
    label: '마이크',
    description: '마이크로 들어오는 소리를 녹음합니다.',
    icon: Mic2,
  },
  {
    id: 'file',
    label: '음성 파일',
    description: '기존 음성 파일 하나를 전사합니다.',
    icon: FileAudio2,
  },
]

const EXAMPLES = [
  { title: '온라인 강의', meta: '시스템 소리 · 2026. 10. 27. · 65분', status: '한도 대기', stage: 'waiting' },
  { title: '지난 회의', meta: '마이크 · 2026. 10. 26. · 24분', status: '일시 정지', stage: 'paused' },
  { title: '팀 미팅', meta: '시스템 소리 · 2026. 10. 27. · 42분', text: '오늘 논의할 내용부터 정리해 볼게요. 먼저 진행 상황을 공유하고 다음 단계에 대해 이야기하겠습니다.' },
  { title: '아이디어 메모', meta: '마이크 · 2026. 10. 26. · 8분', text: '생각난 아이디어를 간단히 기록해 둡니다. 나중에 다시 살펴볼 수 있도록 핵심 내용을 정리합니다.' },
  { title: 'interview.m4a', meta: '음성 파일 · 2026. 10. 25. · 31분', text: '안녕하세요. 오늘은 준비한 질문을 중심으로 이야기를 나눠보겠습니다.' },
  { title: '강의 녹음', meta: '시스템 소리 · 2026. 10. 24. · 56분', stopped: true },
]

function MainVariantA({ onSelect, onOpen, transcripts }) {
  return (
    <main className="simple-stt-main simple-stt-main--cards">
      <div className="simple-stt-card-grid" aria-label="음성 입력 방식">
        {SOURCES.map((source) => {
          const Icon = source.icon
          return (
            <button
              key={source.id}
              className="simple-stt-card"
              onClick={() => onSelect(source.id)}
              type="button"
            >
              <span className="simple-stt-card__icon"><Icon size={21} strokeWidth={1.8} /></span>
              <span className="simple-stt-card__copy"><strong>{source.label}</strong><small>{source.description}</small></span>
            </button>
          )
        })}
      </div>
      <section className="simple-stt-history" aria-label="전사 목록">
        <h2>전사 목록</h2>
        <div className="simple-stt-history__list">
          {transcripts.map((item) => <div className="simple-stt-history__row" key={item.meta}>
            <button className="simple-stt-history__open" onClick={() => onOpen(item)} type="button"><strong>{item.title} {(item.stopped || item.status) && <span className="simple-stt-history__badge">{item.status || '정지됨'}</span>}</strong><small>{item.meta}</small></button>
            {!item.stopped && !item.status && <details className="simple-stt-history__download"><summary aria-label={`${item.title} 다운로드`}><Download size={17} /></summary><div><button type="button" onClick={() => window.alert('초안에서는 다운로드가 연결되지 않았습니다.')}>MD 다운로드</button><button type="button" onClick={() => window.alert('초안에서는 다운로드가 연결되지 않았습니다.')}>TXT 다운로드</button></div></details>}
          </div>)}
        </div>
      </section>
    </main>
  )
}

function MainVariantB({ onSelect }) {
  return (
    <main className="simple-stt-main simple-stt-main--list">
      <div className="simple-stt-source-list" role="group" aria-label="음성 입력 방식">
        {SOURCES.map((source) => {
          const Icon = source.icon
          return (
            <button
              key={source.id}
              className="simple-stt-source-row"
              onClick={() => onSelect(source.id)}
              type="button"
            >
              <span className="simple-stt-source-row__icon"><Icon size={19} /></span>
              <span className="simple-stt-source-row__copy"><strong>{source.label}</strong><small>{source.description}</small></span>
            </button>
          )
        })}
      </div>
    </main>
  )
}

function MainVariantC({ selected, onSelect }) {
  return (
    <main className="simple-stt-main simple-stt-main--tabs">
      <div className="simple-stt-tabs-intro">
        <div className="simple-stt-tabs-panel">
          <Tabs.Root value={selected ?? ''} onValueChange={onSelect} orientation="vertical" activationMode="manual">
            <Tabs.List className="simple-stt-tabs" aria-label="음성 입력 방식">
              {SOURCES.map((source) => {
                const Icon = source.icon
                return <Tabs.Trigger key={source.id} className="simple-stt-tabs__trigger" value={source.id}><Icon size={18} /><span className="simple-stt-tabs__copy"><strong>{source.label}</strong><small>{source.description}</small></span></Tabs.Trigger>
              })}
            </Tabs.List>
            {SOURCES.map((source) => <Tabs.Content key={source.id} value={source.id} className="simple-stt-sr-only">{source.label} 선택됨</Tabs.Content>)}
          </Tabs.Root>
        </div>
      </div>
    </main>
  )
}

function VariantSwitcher({ onReset, showReset }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const currentKey = searchParams.get('variant')?.toUpperCase() ?? 'A'
  const currentIndex = Math.max(0, VARIANTS.findIndex((item) => item.key === currentKey))
  const current = VARIANTS[currentIndex]
  const navigate = (offset) => {
    const next = VARIANTS[(currentIndex + offset + VARIANTS.length) % VARIANTS.length]
    setSearchParams({ variant: next.key })
  }

  useEffect(() => {
    const onKeyDown = (event) => {
      const target = event.target
      const isEditable = target instanceof HTMLElement && (
        ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable || target.closest('[role="tablist"]')
      )
      if (isEditable || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault()
      navigate(event.key === 'ArrowLeft' ? -1 : 1)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [currentIndex])

  return (
    <nav className="simple-stt-switcher" aria-label="화면 초안 전환">
      {showReset && <button onClick={onReset} type="button" aria-label="초안의 처음 화면"><RotateCcw size={15} /></button>}
      <button onClick={() => navigate(-1)} type="button" aria-label="이전 화면"><ArrowLeft size={15} /></button>
      <span><b>{current.key}</b><i />{current.name}<small>초안</small></span>
      <button onClick={() => navigate(1)} type="button" aria-label="다음 화면"><ArrowRight size={15} /></button>
    </nav>
  )
}

export default function GroqFreeSTTFlowPrototype({ variant }) {
  const normalizedVariant = VARIANTS.some((item) => item.key === variant) ? variant : 'A'
  const [searchParams, setSearchParams] = useSearchParams()
  const preview = searchParams.get('preview')
  const [stage, setStage] = useState(() => ['recording', 'transcribing', 'waiting', 'paused', 'completed'].includes(preview) ? preview : 'home')
  const [selected, setSelected] = useState(() => preview === 'recording' ? (searchParams.get('source') === 'mic' ? 'mic' : 'system') : null)
  const [fileName, setFileName] = useState('')
  const [transcripts, setTranscripts] = useState(EXAMPLES)
  const [openedTranscript, setOpenedTranscript] = useState(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [draftTitle, setDraftTitle] = useState('')
  const fileInputRef = useRef(null)

  const onSelect = (source) => {
    if (source === 'file') {
      fileInputRef.current?.click()
      return
    }
    setSelected(source)
    setStage('recording')
  }

  const onFileChange = (event) => {
    const file = event.target.files?.[0]
    if (file) {
      setFileName(file.name)
      setSelected('file')
      setStage('transcribing')
    }
    event.target.value = ''
  }

  const onReset = () => {
    setStage('home')
    setSelected(null)
    setFileName('')
    setOpenedTranscript(null)
    setEditingTitle(false)
    const next = new URLSearchParams(searchParams)
    next.delete('preview')
    next.delete('source')
    setSearchParams(next, { replace: true })
  }

  const sourceLabel = selected === 'system' ? '시스템 소리' : selected === 'mic' ? '마이크' : fileName || '음성 파일'

  return (
    <div className="simple-stt-prototype">
      {stage === 'home' && normalizedVariant === 'A' && <MainVariantA onSelect={onSelect} transcripts={transcripts} onOpen={(item) => { setOpenedTranscript(item); if (item.stage) { setFileName(item.title); setSelected('file') } setStage(item.stage || 'transcript') }} />}
      {stage === 'transcript' && openedTranscript && <main className="simple-stt-transcript">
        <button type="button" onClick={onReset} className="simple-stt-transcript__back"><ArrowLeft size={17} /> 뒤로 가기</button>
        {editingTitle ? <form className="simple-stt-transcript__edit" onSubmit={(event) => { event.preventDefault(); const title = draftTitle.trim(); if (!title) return; const updated = { ...openedTranscript, title }; setTranscripts((items) => items.map((item) => item === openedTranscript ? updated : item)); setOpenedTranscript(updated); setEditingTitle(false) }}>
          <label className="simple-stt-sr-only" htmlFor="transcript-title">전사 제목</label><input id="transcript-title" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} autoFocus /><button type="button" onClick={() => setEditingTitle(false)}>취소</button><button type="submit" disabled={!draftTitle.trim()}>저장</button>
        </form> : <div className="simple-stt-transcript__heading"><h1>{openedTranscript.title}</h1><button type="button" aria-label="제목 수정" onClick={() => { setDraftTitle(openedTranscript.title); setEditingTitle(true) }}><Pencil size={16} /></button></div>}
        <p className="simple-stt-transcript__meta">{openedTranscript.meta}</p>{openedTranscript.stopped ? <div className="simple-stt-transcript__stopped"><p>전사가 정지되었습니다. 보관된 원본으로 처음부터 다시 전사할 수 있습니다.</p><button type="button" onClick={() => { setFileName(openedTranscript.title); setSelected('file'); setStage('transcribing') }}>다시 전사</button></div> : <><p className="simple-stt-transcript__text"><span>00:00:00</span> {openedTranscript.text}</p><p className="simple-stt-transcript__note">예시 전사 내용입니다.</p></>}
      </main>}
      {stage === 'home' && normalizedVariant === 'B' && <MainVariantB onSelect={onSelect} />}
      {stage === 'home' && normalizedVariant === 'C' && <MainVariantC selected={selected} onSelect={onSelect} />}
      {stage === 'recording' && <RecordingStatusPrototype source={selected} preview={preview === 'recording'} onStop={() => setStage('transcribing')} />}
      {['transcribing', 'waiting', 'paused', 'completed'].includes(stage) && <TranscriptionStatusPrototype key={stage} sourceLabel={sourceLabel} initialState={stage === 'waiting' ? 'limit' : stage === 'transcribing' ? 'active' : stage} onHome={onReset} onViewHome={() => setStage('home')} />}
      <input ref={fileInputRef} type="file" accept=".flac,.mp3,.mp4,.mpeg,.mpga,.m4a,.ogg,.wav,.webm" hidden onChange={onFileChange} />
      <VariantSwitcher onReset={onReset} showReset={stage !== 'home'} />
    </div>
  )
}

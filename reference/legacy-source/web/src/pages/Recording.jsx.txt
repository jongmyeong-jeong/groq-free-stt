import { useCallback, useEffect, useRef, useState } from 'react'

import api from '@/api/client'
import { useAppStore } from '@/stores/app'
import useSystemInfo from '@/hooks/useSystemInfo'
import FlipCardTimer from '@/components/FlipCardTimer'
import RecIndicator from '@/components/RecIndicator'
import AudioLevelMeter from '@/components/AudioLevelMeter'
import ErrorModal from '@/components/ErrorModal'
import IdleScreen from '@/components/IdleScreen'
import StopButton from '@/components/StopButton'
import TranscribingView from '@/components/TranscribingView'
import DoneView from '@/components/DoneView'

// Plan §4.2 + AC-7. Chunk cadence is 10s; MediaRecorder mimeType is fixed.
const CHUNK_MS = 10000
const MIME_TYPE = 'audio/webm;codecs=opus'

const DARK_PAGE_STYLE = {
  minHeight: '100vh',
  background: '#09090b',
  color: '#fafafa',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
}

const RECORDING_LAYOUT = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '64px',
}

const RECORDING_HEADER = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '40px',
}

const RECORDING_MIDDLE = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '48px',
}

function classifyMicError(err) {
  const name = err?.name ?? ''
  if (!navigator.mediaDevices) {
    return { errorType: 'mic_https_required' }
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return { errorType: 'mic_not_found' }
  }
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return { errorType: 'mic_denied' }
  }
  return {
    errorType: 'mic_error',
    description: `${name || 'Unknown'}: ${err?.message ?? ''}`.trim(),
  }
}

export default function Recording() {
  const { data: sysInfo } = useSystemInfo()
  const setRecording = useAppStore((s) => s.setRecording)
  const resetRecording = useAppStore((s) => s.resetRecording)

  // UI state
  const [recordingState, setRecordingState] = useState('idle') // 'idle' | 'recording' | 'transcribing' | 'done'
  const [elapsedMs, setElapsedMs] = useState(0)
  const [stream, setStream] = useState(null)
  const [noteId, setNoteId] = useState(null)
  const [partialFailure, setPartialFailure] = useState(false)

  // Error modal state — description is an optional body override
  const [errorModal, setErrorModal] = useState({ open: false, errorType: null, description: null })

  // Refs for async callbacks / lifecycle
  const mediaRecorderRef = useRef(null)
  const streamRef = useRef(null)
  const seqRef = useRef(0)
  const sessionIdRef = useRef(null)
  const startedAtRef = useRef(null)
  const noteIdRef = useRef(null)
  const timerRef = useRef(null)
  const pendingUploadsRef = useRef(new Set())
  const stopInFlightRef = useRef(false)

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const stopTracks = useCallback(() => {
    const s = streamRef.current
    if (s) s.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setStream(null)
  }, [])

  const trackPendingUpload = useCallback((promise) => {
    pendingUploadsRef.current.add(promise)
    promise.finally(() => pendingUploadsRef.current.delete(promise))
    return promise
  }, [])

  const waitForPendingUploads = useCallback(async () => {
    const pending = Array.from(pendingUploadsRef.current)
    if (pending.length === 0) return
    await Promise.allSettled(pending)
  }, [])

  const uploadChunk = useCallback(async (sessionId, blob, seq) => {
    try {
      const res = await api.postRecordingChunk(sessionId, blob, seq)
      if (res?.noteId) {
        noteIdRef.current = res.noteId
        setNoteId(res.noteId)
      }
    } catch (err) {
      if (err?.status === 409) {
        // Duplicate seq — backend already has it. Skip silently.
        return
      }
      // Non-critical upload failure — log and continue; don't interrupt recording
      console.warn('chunk upload failed', seq, err?.message)
    }
  }, [])

  const openErrorModal = useCallback((errorType, description = null) => {
    setErrorModal({ open: true, errorType, description })
  }, [])

  const closeErrorModal = useCallback(() => {
    setErrorModal({ open: false, errorType: null, description: null })
  }, [])

  const handleStart = useCallback(async () => {
    if (recordingState !== 'idle') return
    stopInFlightRef.current = false

    // GROQ_API_KEY guard — check before acquiring mic
    if (sysInfo?.groqConfigured === false) {
      openErrorModal('api_key_missing')
      return
    }

    // Acquire mic
    let micStream
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err) {
      const { errorType, description } = classifyMicError(err)
      openErrorModal(errorType, description)
      return
    }

    // Create recording session
    let session
    try {
      session = await api.createRecording({})
    } catch (err) {
      micStream.getTracks().forEach((t) => t.stop())
      if (err?.status === 503 && err?.body?.error === 'groq_api_key_missing') {
        openErrorModal('api_key_missing')
      } else {
        openErrorModal('session_create_failed', err?.message ?? null)
      }
      return
    }

    // Create MediaRecorder
    let mr
    try {
      mr = new MediaRecorder(micStream, { mimeType: MIME_TYPE })
    } catch (err) {
      micStream.getTracks().forEach((t) => t.stop())
      openErrorModal('browser_unsupported_recorder')
      return
    }

    seqRef.current = 0
    sessionIdRef.current = session.id
    noteIdRef.current = null
    mediaRecorderRef.current = mr
    streamRef.current = micStream
    pendingUploadsRef.current.clear()
    setStream(micStream)
    setNoteId(null)

    mr.ondataavailable = (ev) => {
      if (!ev.data || ev.data.size === 0) return
      const seq = seqRef.current
      seqRef.current = seq + 1
      trackPendingUpload(uploadChunk(session.id, ev.data, seq))
    }

    mr.onerror = (ev) => {
      console.warn('MediaRecorder error', ev?.error?.message)
    }

    const startedAt = Date.now()
    startedAtRef.current = startedAt
    setElapsedMs(0)
    clearTimer()
    timerRef.current = setInterval(() => {
      setElapsedMs(Date.now() - startedAt)
    }, 1000)

    setRecordingState('recording')
    setRecording({ sessionId: session.id, startedAt, elapsedSec: 0, status: 'recording', error: null })

    mr.start(CHUNK_MS)
  }, [recordingState, sysInfo, uploadChunk, trackPendingUpload, clearTimer, setRecording, openErrorModal])

  const handleStop = useCallback(async () => {
    if (stopInFlightRef.current) return
    const mr = mediaRecorderRef.current
    const sessionId = sessionIdRef.current
    const startedAt = startedAtRef.current
    if (!mr || !sessionId || startedAt === null) return
    stopInFlightRef.current = true

    clearTimer()
    setElapsedMs(0)

    const stopped = new Promise((resolve) => {
      const prev = mr.ondataavailable
      mr.ondataavailable = (ev) => { if (prev) prev(ev) }
      mr.onstop = () => resolve()
    })
    if (mr.state !== 'inactive') {
      try { mr.stop() } catch { /* ignore */ }
    }
    await stopped
    await waitForPendingUploads()

    stopTracks()
    mediaRecorderRef.current = null

    const durationSec = (Date.now() - startedAt) / 1000

    if (durationSec < 1) {
      openErrorModal('recording_too_short')
      resetRecording()
      sessionIdRef.current = null
      startedAtRef.current = null
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
      setRecordingState('idle')
      return
    }

    const _resetState = () => {
      resetRecording()
      sessionIdRef.current = null
      startedAtRef.current = null
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
    }
    const _captureNoteId = (evt) => {
      const nid = evt?.payload?.noteId ?? noteIdRef.current
      if (nid) {
        noteIdRef.current = nid
        setNoteId(nid)
      }
    }
    const _transitionTo = (next) => {
      _resetState()
      setRecordingState(next)
    }

    // Normal finalize path: show transcribing screen, then done.
    //
    // The disposer returned by api.finalizeRecording is intentionally NOT
    // captured. The server treats finalize as detached from the client's
    // connection lifetime — the .md file is written even if the user closes
    // the tab mid-transcribe (see app/server.py finalize producer, which only
    // logs disconnects). Aborting the in-flight POST here would cancel that
    // work, so we let the request run to completion in the background.
    //
    // `finalizeSettled` guards against transportError firing after a
    // complete/error event has already resolved the finalize flow. Without it,
    // a network hiccup between the complete event and stream EOF would open a
    // spurious transport_error modal on top of the done screen.
    let finalizeSettled = false
    setRecordingState('transcribing')
    api.finalizeRecording(
      sessionId,
      { durationSec },
      {
        complete: (evt) => {
          finalizeSettled = true
          _captureNoteId(evt)
          const pf = evt?.payload?.partialFailure ?? false
          setPartialFailure(pf)
          _transitionTo('done')
        },
        error: (evt) => {
          finalizeSettled = true
          const errType = evt?.payload?.error === 'recording too short'
            ? 'recording_too_short'
            : 'transcription_failed'
          const desc = evt?.payload?.description ?? null
          openErrorModal(errType, desc)
          setPartialFailure(false) // clear any stale banner from a prior recording
          _transitionTo('idle')
        },
        transportError: () => {
          if (finalizeSettled) return
          openErrorModal('transport_error')
          setPartialFailure(false)
          _transitionTo('idle')
        },
      },
    )
  }, [clearTimer, stopTracks, resetRecording, waitForPendingUploads, openErrorModal])

  const handleStopFromModal = useCallback(() => {
    closeErrorModal()
    handleStop()
  }, [closeErrorModal, handleStop])

  const handleDownload = useCallback(async () => {
    const nid = noteIdRef.current ?? noteId
    if (!nid) return
    try {
      await api.downloadNote(nid)
    } catch (err) {
      openErrorModal('download_failed', err?.message ?? null)
    }
  }, [noteId, openErrorModal])

  // beforeunload: warn user and try to flush a final chunk
  useEffect(() => {
    const handler = (ev) => {
      if (recordingState !== 'recording') return
      const mr = mediaRecorderRef.current
      if (mr && mr.state === 'recording') {
        try { mr.requestData() } catch { /* ignore */ }
      }
      ev.preventDefault()
      ev.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [recordingState])

  // Unmount cleanup
  useEffect(() => {
    return () => {
      clearTimer()
      const mr = mediaRecorderRef.current
      if (mr && mr.state !== 'inactive') {
        try { mr.stop() } catch { /* ignore */ }
      }
      const s = streamRef.current
      if (s) s.getTracks().forEach((t) => t.stop())
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
    }
  }, [clearTimer])

  const handleGoHome = useCallback(() => {
    setRecordingState('idle')
    setNoteId(null)
    setPartialFailure(false)
  }, [])

  return (
    <>
      {recordingState === 'idle' && <IdleScreen onStart={handleStart} />}

      {recordingState === 'recording' && (
        <div style={DARK_PAGE_STYLE}>
          <div style={RECORDING_LAYOUT}>
            <div style={RECORDING_HEADER}>
              <RecIndicator pulsing />
              <FlipCardTimer elapsedMs={elapsedMs} />
            </div>
            <div style={RECORDING_MIDDLE}>
              <AudioLevelMeter audioStream={stream} />
              <StopButton onClick={handleStop} />
            </div>
          </div>
        </div>
      )}

      {recordingState === 'transcribing' && (
        <div style={DARK_PAGE_STYLE}>
          <TranscribingView />
        </div>
      )}

      {recordingState === 'done' && (
        <div style={DARK_PAGE_STYLE}>
          <DoneView
            noteId={noteId}
            partialFailure={partialFailure}
            onDownload={handleDownload}
            onGoHome={handleGoHome}
          />
        </div>
      )}

      <ErrorModal
        open={errorModal.open}
        errorType={errorModal.errorType}
        description={errorModal.description}
        onClose={closeErrorModal}
        onStopRecording={handleStopFromModal}
      />
    </>
  )
}

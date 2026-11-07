import { useEffect, useRef, useState } from 'react'
import './AudioLevelMeter.css'

const NUM_BARS = 30
const FLAT_HEIGHT = 6 // resting height — matches bar width so each bar is a circle
const MAX_BAR_HEIGHT = 48

// Scrolling-level tuning.
// - Loudness (RMS of the time-domain waveform, 0..1) below SILENCE_LEVEL is
//   treated as mic noise floor and rests at FLAT_HEIGHT.
// - Normal speech RMS sits around 0.05–0.3, so LEVEL_GAIN×level saturates the
//   bar near a raised voice; the 0.8 exponent lifts quiet speech perceptually.
// - The strip scrolls one bar every SCROLL_EVERY_N_FRAMES rAF frames
//   (~30 columns/s at 60 fps → a full sweep across 30 bars in ~1 s).
const SILENCE_LEVEL = 0.015
const LEVEL_GAIN = 4
const LEVEL_EXPONENT = 0.8
const EMA_ALPHA = 0.5
const SCROLL_EVERY_N_FRAMES = 2

// Stable per-bar opacity (Figma node 492:160) — gives visual variety
// without re-randomising every render.
const BAR_OPACITIES = [
  0.78, 0.72, 0.79, 0.6, 0.92, 0.65, 0.98, 0.6, 0.75, 0.63,
  0.85, 0.6, 0.64, 0.61, 0.69, 0.87, 0.74, 0.73, 0.72, 0.77,
  0.97, 0.9, 0.67, 0.61, 0.64, 0.65, 0.61, 0.68, 0.67, 0.86,
]

const FLAT_HEIGHTS = new Array(NUM_BARS).fill(FLAT_HEIGHT)

function levelToHeight(level) {
  if (level < SILENCE_LEVEL) return FLAT_HEIGHT
  const norm = Math.pow(Math.min(1, level * LEVEL_GAIN), LEVEL_EXPONENT)
  return FLAT_HEIGHT + norm * (MAX_BAR_HEIGHT - FLAT_HEIGHT)
}

/**
 * AudioLevelMeter
 *
 * Scrolling loudness history (Voice-Memos style): each bar is one moment in
 * time — the current mic level enters on the right and flows left, so the
 * whole strip animates while speaking. A frequency-spectrum mapping was tried
 * first but voice energy concentrates below ~1 kHz, which kept everything
 * right of the first few bars permanently flat.
 *
 * Props:
 *   audioStream?: MediaStream | null
 */
export default function AudioLevelMeter({ audioStream }) {
  const [barHeights, setBarHeights] = useState(FLAT_HEIGHTS)
  const rafRef = useRef(null)

  useEffect(() => {
    if (!audioStream) {
      setBarHeights(FLAT_HEIGHTS)
      return undefined
    }

    const AudioCtx = window.AudioContext || window.webkitAudioContext
    if (!AudioCtx) {
      setBarHeights(FLAT_HEIGHTS)
      return undefined
    }

    const audioContext = new AudioCtx()
    let source
    let analyser
    try {
      source = audioContext.createMediaStreamSource(audioStream)
      analyser = audioContext.createAnalyser()
      // Time-domain window: 1024 samples ≈ 21 ms @ 48 kHz — steady RMS.
      analyser.fftSize = 1024
      source.connect(analyser)
      // Autoplay policy: contexts created outside a direct user gesture start
      // 'suspended', and a suspended analyser reads all-zero levels (flat bars).
      if (audioContext.state === 'suspended') {
        audioContext.resume().catch(() => {})
      }
    } catch (err) {
      console.warn('AudioLevelMeter: cannot attach stream', err)
      setBarHeights(FLAT_HEIGHTS)
      audioContext.close().catch(() => {})
      return undefined
    }

    const timeBuffer = new Uint8Array(analyser.fftSize)
    const levels = new Array(NUM_BARS).fill(0)
    let smoothedLevel = 0
    let frame = 0

    const tick = () => {
      rafRef.current = requestAnimationFrame(tick)
      analyser.getByteTimeDomainData(timeBuffer)

      // RMS of the waveform around the 128 midpoint → loudness 0..1.
      let sumSquares = 0
      for (let i = 0; i < timeBuffer.length; i++) {
        const deviation = (timeBuffer[i] - 128) / 128
        sumSquares += deviation * deviation
      }
      const rms = Math.sqrt(sumSquares / timeBuffer.length)
      smoothedLevel = EMA_ALPHA * rms + (1 - EMA_ALPHA) * smoothedLevel

      // Advance the strip every Nth frame: drop the oldest (left), push the
      // newest level (right).
      frame += 1
      if (frame % SCROLL_EVERY_N_FRAMES !== 0) return
      levels.shift()
      levels.push(smoothedLevel)
      setBarHeights(levels.map(levelToHeight))
    }

    rafRef.current = requestAnimationFrame(tick)

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      try {
        source.disconnect()
        analyser.disconnect()
      } catch {
        // already disconnected
      }
      audioContext.close().catch(() => {})
    }
  }, [audioStream])

  return (
    <div className="audio-level-meter">
      {barHeights.map((h, i) => (
        <div
          key={i}
          className="audio-level-meter__bar"
          style={{ height: `${h}px`, opacity: BAR_OPACITIES[i] }}
        />
      ))}
    </div>
  )
}

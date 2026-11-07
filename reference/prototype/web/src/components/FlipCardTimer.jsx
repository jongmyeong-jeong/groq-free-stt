import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import './FlipCardTimer.css'

/**
 * FlipDigit — full-card flip per digit change, driven by Framer Motion.
 *
 * AnimatePresence keeps the previous card mounted long enough to play its
 * exit animation (rotateX 0 → -90deg) while the new card enters from
 * rotateX(90deg). Spring physics gives the natural overshoot/settle that
 * CSS keyframes can only approximate.
 */
function FlipDigit({ digit }) {
  return (
    <span className="flip-digit" aria-hidden="true">
      <AnimatePresence>
        <motion.span
          key={digit}
          initial={{ rotateX: 90, filter: 'brightness(0.3)', zIndex: 10 }}
          animate={{ rotateX: 0, filter: 'brightness(1)', zIndex: 20 }}
          exit={{ rotateX: -90, filter: 'brightness(0.3)', zIndex: 0 }}
          transition={{ duration: 0.5, type: 'spring', stiffness: 100, damping: 15 }}
          className="flip-digit__card"
          style={{ transformOrigin: 'center', backfaceVisibility: 'hidden' }}
        >
          <span className="flip-digit__shade" />
          <span className="flip-digit__num">{digit}</span>
          <span className="flip-digit__seam" />
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

function formatHumanReadable(elapsedMs) {
  const totalSeconds = Math.floor(elapsedMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)

  if (hours > 0) {
    return `${hours}시간 ${minutes}분`
  }
  if (minutes > 0) {
    return `${minutes}분`
  }
  return '0분'
}

function Separator() {
  return (
    <span className="flip-timer__sep" aria-hidden="true">
      <span className="flip-timer__dot" />
      <span className="flip-timer__dot" />
    </span>
  )
}

/**
 * FlipCardTimer — elapsed time display with per-digit full-card flip.
 */
export default function FlipCardTimer({ elapsedMs = 0, forceHours = false }) {
  const totalSeconds = Math.floor(elapsedMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  const showHours = forceHours || elapsedMs >= 3_600_000

  const hh0 = String(Math.floor(hours / 10))
  const hh1 = String(hours % 10)
  const mm0 = String(Math.floor(minutes / 10))
  const mm1 = String(minutes % 10)
  const ss0 = String(Math.floor(seconds / 10))
  const ss1 = String(seconds % 10)

  const hhVisibleRef = useRef(showHours)
  const [hhSegmentVisible, setHhSegmentVisible] = useState(showHours)

  useEffect(() => {
    if (showHours && !hhVisibleRef.current) {
      hhVisibleRef.current = true
      setHhSegmentVisible(true)
    }
  }, [showHours])

  const totalMinutes = Math.floor(elapsedMs / 60_000)
  const [ariaLabel, setAriaLabel] = useState(() => `경과 시간 ${formatHumanReadable(elapsedMs)}`)

  useEffect(() => {
    setAriaLabel(`경과 시간 ${formatHumanReadable(elapsedMs)}`)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalMinutes])

  return (
    <div
      className="flip-timer"
      role="timer"
      aria-live="polite"
      aria-label={ariaLabel}
    >
      {(showHours || hhSegmentVisible) && (
        <span
          className={`flip-timer__hh-segment${hhSegmentVisible ? ' flip-timer__hh-segment--visible' : ''}`}
        >
          <FlipDigit digit={hh0} />
          <FlipDigit digit={hh1} />
          <Separator />
        </span>
      )}

      <FlipDigit digit={mm0} />
      <FlipDigit digit={mm1} />

      <Separator />

      <FlipDigit digit={ss0} />
      <FlipDigit digit={ss1} />
    </div>
  )
}

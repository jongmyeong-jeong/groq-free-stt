import { Suspense, lazy, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import useSystemInfo from '@/hooks/useSystemInfo'
import ErrorModal from '@/components/ErrorModal'

const Recording = lazy(() => import('@/pages/Recording'))

const LOADING_STYLE = {
  minHeight: '100vh',
  width: '100%',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: '#09090b',
  color: '#71717a',
  fontFamily: 'Inter, sans-serif',
  fontSize: '14px',
  letterSpacing: '-0.01em',
}

function FullScreenLoading({ label }) {
  return <section style={LOADING_STYLE}>{label}</section>
}

// Shows a one-time modal when GROQ_API_KEY is not configured.
// User can dismiss and still see Recording page, but the start button will
// enforce the same check when clicked (done in Recording.jsx).
function GroqKeyGate({ children }) {
  const { data: sysInfo, isLoading } = useSystemInfo()
  const [dismissed, setDismissed] = useState(false)

  if (isLoading) {
    return <FullScreenLoading label="불러오는 중..." />
  }

  const keyMissing = sysInfo && sysInfo.groqConfigured === false

  return (
    <>
      {children}
      <ErrorModal
        open={keyMissing && !dismissed}
        errorType="api_key_missing"
        onClose={() => setDismissed(true)}
      />
    </>
  )
}

export default function App() {
  const page = (
    <Suspense fallback={<FullScreenLoading label="화면을 불러오는 중..." />}>
      <Routes>
        <Route path="/" element={<Recording />} />
        <Route path="*" element={<Recording />} />
      </Routes>
    </Suspense>
  )

  const requestedVariant = new URLSearchParams(window.location.search).get('variant')?.toUpperCase()
  const isFlowPrototype = import.meta.env.DEV && ['A', 'B', 'C'].includes(requestedVariant)

  if (isFlowPrototype) return page

  return <GroqKeyGate>{page}</GroqKeyGate>
}

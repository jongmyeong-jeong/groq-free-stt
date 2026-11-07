import { create } from 'zustand'

// App-level Zustand store only keeps transient recording UI state.
// Server-backed data lives in TanStack Query.

const initialRecording = {
  sessionId: null,
  startedAt: null,
  elapsedSec: 0,
  status: 'idle',
  error: null,
}

export const useAppStore = create((set) => ({
  recording: initialRecording,

  setRecording(patch) {
    set((state) => ({ recording: { ...state.recording, ...patch } }))
  },

  resetRecording() {
    set({ recording: initialRecording })
  },
}))

export default useAppStore

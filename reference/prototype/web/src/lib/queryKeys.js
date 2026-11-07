// TanStack Query keys per plan §4.5 (post-groq migration).
// Only recording-related keys and system info are retained.

export const qk = {
  systemInfo: () => ['system', 'info'],
}

export const mk = {
  createRecording: 'createRecording',
  finalizeRecording: 'finalizeRecording',
}

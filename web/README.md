# Microphone recording foundation

`MicrophoneRecording.start` requests audio permission and accepts a storage adapter
with asynchronous `start(mimeType)`, `append(id, sequence, blob, elapsedSeconds)`,
and `finish(id, elapsedSeconds)` methods. It emits one-second MediaRecorder chunks
in order and reports monotonic elapsed time including pauses. A rejected write
prevents finish, leaving stored fragments available for recovery. The caller must
surface permission and storage failures and stop on page lifecycle events.

`RecordingStore` persists these fragments in a separate local SQLite database.
On startup, interrupted sessions with fragments are `recovery_pending`, not
confirmed playable; empty sessions are `recovery_failed`. Validation, conversion,
recovery UI, a transport adapter, and shutdown/page-close coordination are not yet
implemented. Chunks delivered or uploaded after abrupt termination can be lost;
MediaRecorder container fragments are not guaranteed to be independently playable.

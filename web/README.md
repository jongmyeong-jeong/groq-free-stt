# Microphone recording integration

`MicrophoneRecording.start({storage: recordingStorage()})` requests microphone
permission and begins one-second MediaRecorder chunks. Its `pause`, `resume`,
`elapsed` and async `stop` methods control capture. Elapsed time uses a monotonic
clock and includes pauses. The caller surfaces permission and storage errors and
registers `stopOnPageHide(recording)`; page teardown is best effort and pending
chunks can be lost. A failed write prevents marking a recording finished.

`GET /recordings` returns recordings without creating transcription jobs. On
server restart interrupted sessions with fragments become `recovery_pending`;
empty sessions become `recovery_failed`. For each pending item the home UI calls
`validateRecoveredRecording(storage, id, audioContext)` to decode the preserved
source and mark it `recovered` or `recovery_failed`. A failure retains its source.
The UI should present recovered recordings and only create a transcription job
when selected. `storage.source(id)` returns the base64 source. The browser decode
check is not a guarantee that the entire original capture was saved. The chunk
boundary and browser/server shutdown limits require actual macOS testing.

`make stop` requests capture to stop and waits up to ten seconds for active
recordings to finish before shutting down. The browser polls once per second and
also stops capture when the server disappears. If it cannot finish in time,
committed fragments are recovered at the next startup. Neither abrupt shutdown
nor page teardown guarantees delivery of the last chunk. The UI, permission
guidance and status display still need to be connected and verified on macOS.

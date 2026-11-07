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

The server cannot flush audio still held by a browser when it exits. A coordinated
`make stop` and the UI, including its permission guidance and status display, are
not implemented yet. These must be connected before claiming issue #15 complete.

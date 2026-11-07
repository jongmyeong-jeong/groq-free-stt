import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MicrophoneRecording } from '../web/microphone.mjs';

test('pause includes elapsed time and stop persists ordered chunks before finishing', async () => {
  let now = 0;
  const calls = [];
  class Recorder {
    constructor(stream) { this.stream = stream; this.mimeType = 'audio/webm'; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    pause() { this.state = 'paused'; }
    resume() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.ondataavailable({data: new Blob(['last'])}); this.onstop(); }
  }
  const stream = {getTracks: () => [{stop: () => calls.push('track stopped')}]};
  const recorder = await MicrophoneRecording.start({
    mediaDevices: {getUserMedia: async () => stream}, Recorder, clock: () => now,
    storage: {start: async () => ({id: 'one'}), append: async (id, sequence, data) => calls.push([id, sequence, await data.text()]), finish: async (id, elapsed) => calls.push([id, elapsed])}
  });
  recorder.recorder.ondataavailable({data: new Blob(['first'])});
  now = 2000; recorder.pause();
  now = 7000; recorder.resume();
  now = 10000; await recorder.stop();
  assert.deepEqual(calls, ['track stopped', ['one', 0, 'first'], ['one', 1, 'last'], ['one', 10]]);
});

test('denied permission does not create a recording', async () => {
  let started = false;
  await assert.rejects(MicrophoneRecording.start({
    mediaDevices: {getUserMedia: async () => { throw new Error('denied'); }},
    Recorder: class {}, clock: () => 0,
    storage: {start: async () => { started = true; }}
  }), /denied/);
  assert.equal(started, false);
});

test('a failed chunk prevents marking the recording finished', async () => {
  let finished = false;
  class Recorder {
    constructor() { this.mimeType = 'audio/webm'; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.ondataavailable({data: new Blob(['audio'])}); this.onstop(); }
  }
  const recording = await MicrophoneRecording.start({
    mediaDevices: {getUserMedia: async () => ({getTracks: () => []})}, Recorder, clock: () => 0,
    storage: {start: async () => ({id: 'one'}), append: async () => { throw new Error('disk full'); }, finish: async () => { finished = true; }}
  });
  await assert.rejects(recording.stop(), /disk full/);
  assert.equal(finished, false);
});

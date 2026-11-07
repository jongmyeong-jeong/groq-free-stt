import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MicrophoneRecording, validateRecoveredRecording, stopOnPageHide } from '../web/microphone.mjs';

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
  const stream = {getAudioTracks: () => [], getTracks: () => [{stop: () => calls.push('track stopped')}]};
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

test('pausing mutes audio without removing time from the recorded media', async () => {
  const track = {enabled: true, stop() {}};
  class Recorder {
    constructor() { this.mimeType = 'audio/webm'; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.onstop(); }
  }
  const recording = await MicrophoneRecording.start({
    mediaDevices: {getUserMedia: async () => ({getTracks: () => [track], getAudioTracks: () => [track]})},
    Recorder, clock: () => 0,
    storage: {start: async () => ({id: 'one'}), finish: async () => {}}
  });
  recording.pause();
  assert.equal(track.enabled, false);
  assert.equal(recording.recorder.state, 'recording');
  recording.resume();
  assert.equal(track.enabled, true);
  await recording.stop();
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


test('a failed upload stops the microphone and reports the error immediately', async () => {
  let stopped = false;
  let report;
  const reported = new Promise(resolve => { report = resolve; });
  class Recorder {
    constructor() { this.mimeType = 'audio/webm'; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.onstop(); }
  }
  const recording = await MicrophoneRecording.start({
    mediaDevices: {getUserMedia: async () => ({getTracks: () => [{stop: () => { stopped = true; }}]})},
    Recorder, onError: report,
    storage: {start: async () => ({id: 'one'}), append: async () => { throw new Error('disk full'); }}
  });
  recording.recorder.ondataavailable({data: new Blob(['audio'])});
  assert.match((await reported).message, /disk full/);
  assert.equal(stopped, true);
  await assert.rejects(recording.stop(), /disk full/);
});

test('recovery validation records decoding failure without discarding data', async () => {
  const calls = [];
  const storage = {source: async () => ({source: btoa('broken')}), validate: async (...args) => calls.push(args)};
  await validateRecoveredRecording(storage, 'one', {decodeAudioData: async () => { throw new Error('bad media'); }});
  assert.deepEqual(calls, [['one', false]]);
});

test('pagehide requests recording stop', async () => {
  let listener;
  let stopped = false;
  stopOnPageHide({stop: async () => { stopped = true; }}, {addEventListener: (name, fn) => { listener = fn; }, removeEventListener: () => {}});
  listener();
  assert.equal(stopped, true);
});

test('server shutdown request stops capture and preserves it for recovery', async () => {
  let finished;
  const done = new Promise(resolve => { finished = resolve; });
  let trackStopped = false;
  class Recorder {
    constructor() { this.mimeType = 'audio/webm'; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.ondataavailable({data: new Blob(['audio'])}); this.onstop(); }
  }
  await MicrophoneRecording.start({
    mediaDevices: {getUserMedia: async () => ({getTracks: () => [{stop: () => { trackStopped = true; }}]})},
    Recorder, clock: () => 5000,
    storage: {start: async () => ({id: 'one'}), append: async () => {},
      control: async () => ({stop: true}), finish: async (...args) => finished(args)}
  });
  assert.deepEqual(await done, ['one', 0, true]);
  assert.equal(trackStopped, true);
});

// Storage provides start(mimeType), append(id, sequence, Blob, elapsed), and finish(id, seconds).
export class MicrophoneRecording {
  static async start({storage, mediaDevices = navigator.mediaDevices, Recorder = MediaRecorder, clock = () => performance.now(), onError = () => {}}) {
    const stream = await mediaDevices.getUserMedia({audio: true});
    try {
      const recorder = new Recorder(stream);
      const entry = await storage.start(recorder.mimeType || 'audio/webm');
      return new MicrophoneRecording(stream, recorder, entry.id, storage, clock, onError);
    } catch (error) {
      stream.getTracks().forEach(track => track.stop());
      throw error;
    }
  }

  constructor(stream, recorder, id, storage, clock, onError) {
    this.stream = stream;
    this.recorder = recorder;
    this.id = id;
    this.storage = storage;
    this.clock = clock;
    this.started = clock();
    this.sequence = 0;
    this.pending = Promise.resolve();
    recorder.ondataavailable = event => {
      if (event.data.size) {
        const sequence = this.sequence++;
        const elapsed = this.elapsed();
        this.pending = this.pending.then(() => { if (!this.error) return storage.append(id, sequence, event.data, elapsed); }).catch(error => {
          if (!this.error) {
            this.error = error;
            this.stop(true).catch(() => {});
            onError(error);
          }
        });
      }
    };
    recorder.start(1000);
    if (storage.control) {
      this.monitor = setInterval(async () => {
        if (this.checking || this.stopping) return;
        this.checking = true;
        try {
          if ((await storage.control()).stop) await this.stop(true);
          else if (storage.heartbeat) await storage.heartbeat(id);
        } catch (error) {
          if (!this.error && !this.stopping) {
            this.error = error;
            this.stop(true).catch(() => {});
            onError(error);
          }
        } finally {
          this.checking = false;
        }
      }, 1000);
    }
  }

  elapsed() { return Math.max(0, (this.clock() - this.started) / 1000); }
  pause() { if (!this.stopping) this.stream.getAudioTracks().forEach(track => { track.enabled = false; }); }
  resume() { if (!this.stopping) this.stream.getAudioTracks().forEach(track => { track.enabled = true; }); }
  stop(recover = false) {
    if (this.stopping) return this.stopping;
    const elapsed = this.elapsed();
    clearInterval(this.monitor);
    this.stopping = new Promise((resolve, reject) => {
      this.recorder.onstop = () => {
        this.stream.getTracks().forEach(track => track.stop());
        this.pending.then(async () => {
          if (this.error) {
            try { await this.storage.finish(this.id, elapsed, true); } catch {}
            throw this.error;
          }
          return this.storage.finish(this.id, elapsed, recover);
        }).then(resolve, reject);
      };
      this.recorder.stop();
    });
    return this.stopping;
  }
}

// Same-origin transport for app.py. `keepalive` gives page-close requests a chance
// to complete, but browsers may discard outstanding data during termination.
export function recordingStorage(fetcher = fetch) {
  async function request(path, body, keepalive = true) {
    const response = await fetcher(path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body), keepalive,
    });
    if (!response.ok) throw new Error(`Recording request failed (${response.status})`);
    return response.json();
  }
  return {
    start: mime_type => request('/recordings', {mime_type}),
    append: async (id, sequence, blob, elapsed) => {
      const bytes = new Uint8Array(await blob.arrayBuffer());
      let binary = '';
      for (const byte of bytes) binary += String.fromCharCode(byte);
      return request(`/recordings/${id}/chunks`, {sequence, data: btoa(binary), elapsed}, false);
    },
    finish: (id, elapsed, recover) => request(`/recordings/${id}/finish`, {elapsed, recover}),
    control: () => request('/recording-control'),
    heartbeat: id => request(`/recordings/${id}/heartbeat`, {}),
    list: () => request('/recordings'),
    source: id => request(`/recordings/${id}/source`),
    validate: (id, playable) => request(`/recordings/${id}/validate`, {playable}),
  };
}

// Call for a recovery_pending item before offering it for transcription.
export async function validateRecoveredRecording(storage, id, audioContext) {
  const {source} = await storage.source(id);
  const binary = atob(source);
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
  let playable;
  try {
    await audioContext.decodeAudioData(bytes.buffer);
    playable = true;
  } catch {
    playable = false;
  }
  return storage.validate(id, playable);
}

// Page teardown is best effort: already persisted chunks remain recoverable.
export function stopOnPageHide(recording, target = window) {
  const listener = () => { recording.stop(true).catch(() => {}); };
  target.addEventListener('pagehide', listener, {once: true});
  return () => target.removeEventListener('pagehide', listener);
}

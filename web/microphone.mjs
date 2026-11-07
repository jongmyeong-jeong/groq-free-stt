// Storage provides start(mimeType), append(id, sequence, Blob, elapsed), and finish(id, seconds).
export class MicrophoneRecording {
  static async start({storage, mediaDevices = navigator.mediaDevices, Recorder = MediaRecorder, clock = () => performance.now()}) {
    const stream = await mediaDevices.getUserMedia({audio: true});
    try {
      const recorder = new Recorder(stream);
      const entry = await storage.start(recorder.mimeType || 'audio/webm');
      return new MicrophoneRecording(stream, recorder, entry.id, storage, clock);
    } catch (error) {
      stream.getTracks().forEach(track => track.stop());
      throw error;
    }
  }

  constructor(stream, recorder, id, storage, clock) {
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
        this.pending = this.pending.then(() => { if (!this.error) return storage.append(id, sequence, event.data, elapsed); }).catch(error => { this.error ||= error; });
      }
    };
    recorder.start(1000);
  }

  elapsed() { return Math.max(0, (this.clock() - this.started) / 1000); }
  pause() { if (this.recorder.state === 'recording') this.recorder.pause(); }
  resume() { if (this.recorder.state === 'paused') this.recorder.resume(); }
  stop() {
    if (this.stopping) return this.stopping;
    const elapsed = this.elapsed();
    this.stopping = new Promise((resolve, reject) => {
      this.recorder.onstop = () => {
        this.stream.getTracks().forEach(track => track.stop());
        this.pending.then(() => {
          if (this.error) throw this.error;
          return this.storage.finish(this.id, elapsed);
        }).then(resolve, reject);
      };
      this.recorder.stop();
    });
    return this.stopping;
  }
}

"""Own the native system-audio helper and expose recovery independently of jobs."""
import os
import select
import subprocess
import threading
import time
import uuid
from pathlib import Path

from system_recovery import recover_recordings


class SystemCapture:
    def __init__(self, directory, helper, heartbeat_timeout=15):
        self.directory = Path(directory)
        self.helper = str(helper)
        self.heartbeat_timeout = heartbeat_timeout
        self.lock = threading.RLock()
        self.process = None
        self.recording_id = None
        self.state = 'idle'
        self.heartbeat_at = 0

    def start(self):
        with self.lock:
            if self.process is not None:
                raise RuntimeError('A recording is already active')
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            recording_id = uuid.uuid4().hex
            try:
                process = subprocess.Popen([self.helper, str(self.directory / (recording_id + '.pcm'))],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError as error:
                raise RuntimeError('System audio helper is unavailable') from error
            ready, _, _ = select.select([process.stdout], [], [], 15)
            if not ready or process.stdout.readline().strip() != 'ready':
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()
                if (self.directory / (recording_id + '.pcm')).exists():
                    (self.directory / (recording_id + '.failed')).touch(mode=0o600)
                raise RuntimeError('System audio capture could not start')
            self.process = process
            self.recording_id = recording_id
            self.state = 'recording'
            self.heartbeat_at = time.monotonic()
            threading.Thread(target=self._watch, args=(process,), daemon=True).start()
            return self.status()

    def _watch(self, process):
        while True:
            time.sleep(1)
            with self.lock:
                if self.process is not process:
                    return
                if process.poll() is not None or time.monotonic() - self.heartbeat_at > self.heartbeat_timeout:
                    self.stop()
                    return

    def status(self):
        with self.lock:
            return {'id': self.recording_id, 'status': self.state}

    def heartbeat(self):
        with self.lock:
            if self.process is None:
                raise RuntimeError('No recording is active')
            self.heartbeat_at = time.monotonic()
            return self.status()

    def _command(self, command, expected, state):
        with self.lock:
            if self.process is None or self.state != expected or self.process.poll() is not None:
                raise RuntimeError('Recording is not in the expected state')
            try:
                self.process.stdin.write(command + '\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise RuntimeError('System audio helper stopped') from error
            self.state = state
            return self.status()

    def pause(self):
        return self._command('pause', 'recording', 'paused')

    def resume(self):
        return self._command('resume', 'paused', 'recording')

    def stop(self):
        with self.lock:
            process = self.process
            if process is None:
                return self.status()
            try:
                if process.poll() is None:
                    process.stdin.write('stop\n')
                    process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=10)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            finally:
                if process.returncode != 0:
                    (self.directory / (self.recording_id + '.failed')).touch(mode=0o600)
                process.stdout.close()
                self.process = None
                self.recording_id = None
                self.state = 'idle'
            return self.status()

    def recoveries(self):
        with self.lock:
            if self.process is not None:
                raise RuntimeError('Cannot recover while recording')
            return recover_recordings(self.directory)

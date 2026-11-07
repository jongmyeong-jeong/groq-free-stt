"""Own the native system-audio helper and expose recovery independently of jobs."""
import os
import fcntl
import re
import select
import subprocess
import threading
import time
import uuid
from pathlib import Path

from system_recovery import recover_recordings


class SystemCapture:
    def __init__(self, directory, helper, heartbeat_timeout=15, startup_timeout=300, command_timeout=5):
        self.directory = Path(directory)
        self.helper = str(helper)
        self.heartbeat_timeout = heartbeat_timeout
        self.startup_timeout = startup_timeout
        self.command_timeout = command_timeout
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
            self.process = process
            self.recording_id = recording_id
            self.state = 'starting'
            self.heartbeat_at = time.monotonic()
            result = self.status()
            threading.Thread(target=self._await_ready, args=(process,), daemon=True).start()
            threading.Thread(target=self._watch, args=(process,), daemon=True).start()
            return result

    def _await_ready(self, process):
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            try:
                ready, _, _ = select.select([process.stdout], [], [], .25)
                if ready:
                    line = process.stdout.readline().strip()
                    with self.lock:
                        if self.process is not process:
                            return
                        if line == 'ready' and process.poll() is None:
                            self.state = 'recording'
                        else:
                            self.stop()
                    return
            except (OSError, ValueError):
                return
            with self.lock:
                if self.process is not process:
                    return
        with self.lock:
            if self.process is process:
                self.stop()

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
                ready, _, _ = select.select([self.process.stdout], [], [], self.command_timeout)
                if not ready or self.process.stdout.readline().strip() != 'ack ' + command:
                    raise RuntimeError('System audio helper did not confirm command')
            except (BrokenPipeError, OSError, RuntimeError) as error:
                recording_id = self.recording_id
                self.stop()
                (self.directory / (recording_id + '.failed')).touch(mode=0o600)
                self.state = 'failed'
                self.recording_id = recording_id
                raise RuntimeError('System audio helper did not confirm command') from error
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
            was_starting = self.state == 'starting'
            try:
                if process.poll() is None and was_starting:
                    process.terminate()
                if process.poll() is None and not was_starting:
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
                failed = was_starting or process.returncode != 0
                if failed:
                    (self.directory / (self.recording_id + '.failed')).touch(mode=0o600)
                process.stdout.close()
                self.process = None
                self.state = 'failed' if failed else 'idle'
                if not failed:
                    self.recording_id = None
            return self.status()

    def source(self, recording_id):
        with self.lock:
            item = next((item for item in self.recoveries() if item['id'] == recording_id and item['source'] is not None), None)
            if item is None:
                return None
            lock_fd = os.open(self.directory / (recording_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RuntimeError('Recording is active or being recovered') from error
                try:
                    return (self.directory / item['source']).read_bytes()
                except FileNotFoundError:
                    return None
            finally:
                os.close(lock_fd)

    def delete(self, recording_id):
        if not re.fullmatch(r'[0-9a-f]{32}', recording_id):
            return False
        with self.lock:
            if self.process is not None and self.recording_id == recording_id:
                raise RuntimeError('Recording is active')
            if not self.directory.exists():
                return False
            lock_fd = os.open(self.directory / (recording_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RuntimeError('Recording is active or being recovered') from error
                paths = [self.directory / (recording_id + suffix) for suffix in ('.pcm', '.wav', '.failed')]
                if not any(path.exists() or path.is_symlink() for path in paths):
                    return False
                for path in paths:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                return True
            finally:
                os.close(lock_fd)

    def recoveries(self):
        with self.lock:
            if self.process is not None:
                raise RuntimeError('Cannot recover while recording')
            return recover_recordings(self.directory)

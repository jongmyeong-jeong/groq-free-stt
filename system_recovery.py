"""Recover interrupted 48 kHz mono signed 16-bit PCM recordings without deleting them.

The helper holds an exclusive lock while writing. Failed or interrupted helpers
leave a marker so their partial files cannot be mistaken for successful captures.
"""
import os
import fcntl
import re
import struct
import tempfile
import wave
from pathlib import Path


_RECORDING = re.compile(r'^[0-9a-f]{32}\.pcm$')


def recover_recordings(directory, recording_id=None):
    """Return one outcome per recording; never start a transcription or remove PCM."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    outcomes = []
    candidates = [directory / (recording_id + '.pcm')] if recording_id else sorted(directory.iterdir())
    for original in candidates:
        if not _RECORDING.fullmatch(original.name) or original.is_symlink() or not original.is_file():
            continue
        recording_id = original.stem
        destination = directory / (recording_id + '.wav')
        outcome = {'id': recording_id, 'status': 'failed', 'source': None}
        lock_fd = None
        try:
            lock_fd = os.open(directory / (recording_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                outcome['status'] = 'recording'
                outcomes.append(outcome)
                continue
            capture_failed = (directory / (recording_id + '.failed')).exists()
            temp = None
            try:
                size = original.stat().st_size
                if not size or size % 2:
                    raise ValueError('incomplete PCM frame')
                if not destination.exists():
                    fd, temp = tempfile.mkstemp(prefix='.' + recording_id + '-', suffix='.tmp', dir=directory)
                    with os.fdopen(fd, 'wb') as output:
                        with wave.open(output, 'wb') as wav, original.open('rb') as source:
                            wav.setnchannels(1)
                            wav.setsampwidth(2)
                            wav.setframerate(48000)
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                wav.writeframesraw(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    # Do not overwrite a previously recovered file.
                    try:
                        os.link(temp, destination)
                    except FileExistsError:
                        pass
                if destination.is_symlink():
                    raise ValueError('invalid recovery destination')
                with wave.open(str(destination), 'rb') as wav:
                    if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) != (1, 2, 48000, size // 2):
                        raise ValueError('invalid recovered audio')
                    with original.open('rb') as source:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            if wav.readframes(len(chunk) // 2) != chunk:
                                raise ValueError('recovered audio differs from original')
                outcome.update(status='failed' if capture_failed else 'recovered', source=destination.name)
            except (OSError, ValueError, wave.Error, EOFError, struct.error):
                pass
            finally:
                if temp is not None:
                    try:
                        os.unlink(temp)
                    except FileNotFoundError:
                        pass
        except OSError:
            pass
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
        outcomes.append(outcome)
    return outcomes

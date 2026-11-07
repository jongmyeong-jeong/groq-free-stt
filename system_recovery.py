"""Recover interrupted 48 kHz mono signed 16-bit PCM recordings without deleting them.

This is a recovery primitive only: the capture process and its lifecycle are not
implemented yet. Call it only when no writer owns the recording directory.
"""
import os
import re
import struct
import tempfile
import wave
from pathlib import Path


_RECORDING = re.compile(r'^[0-9a-f]{32}\.pcm$')


def recover_recordings(directory):
    """Return one outcome per recording; never start a transcription or remove PCM."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    outcomes = []
    for original in sorted(directory.iterdir()):
        if not _RECORDING.fullmatch(original.name) or original.is_symlink() or not original.is_file():
            continue
        recording_id = original.stem
        destination = directory / (recording_id + '.wav')
        outcome = {'id': recording_id, 'status': 'failed', 'source': None}
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
            outcome.update(status='recovered', source=destination.name)
        except (OSError, ValueError, wave.Error, EOFError, struct.error):
            pass
        finally:
            if temp is not None:
                try:
                    os.unlink(temp)
                except FileNotFoundError:
                    pass
        outcomes.append(outcome)
    return outcomes

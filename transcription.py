"""Audio splitting, Groq transport and transcription document generation."""
import json
import re
import subprocess
from pathlib import Path


def merge_segments(previous, incoming, offset):
    merged = list(previous)
    for segment in incoming:
        item = {'start': offset + segment['start'], 'end': offset + segment['end'],
                'text': segment['text'].strip()}
        if not item['text']:
            continue
        # Only deduplicate matching text in the overlapping time window.
        if any(old['text'].strip() == item['text'] and old['end'] >= item['start'] - 2
               and old['start'] <= item['end'] + 2 for old in merged):
            continue
        if merged and merged[-1]['end'] >= item['start']:
            old_words = merged[-1]['text'].split()
            new_words = item['text'].split()
            for count in range(min(len(old_words), len(new_words)), 0, -1):
                if old_words[-count:] == new_words[:count]:
                    item['text'] = ' '.join(new_words[count:])
                    break
            if not item['text']:
                continue
        merged.append(item)
    return merged


def timestamp(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def render_transcript(name, imported_at, segments, missing, recorded_at=None):
    header = f'가져온 시각: {imported_at}\n시간 기준: 파일 시작 이후 경과 시간'
    if recorded_at is not None:
        header = f'녹음 시작 시각: {recorded_at}\n시간 기준: 녹음 시작 이후 경과 시간'
    lines = [(s['start'], f"[{timestamp(s['start'])} - {timestamp(s['end'])}] {s['text']}") for s in segments]
    lines += [(start, f'[{timestamp(start)} - {timestamp(end)}] 전사되지 않은 구간') for start, end in missing]
    content = '\n'.join(line for _, line in sorted(lines))
    return {'md': f'# {name}\n\n{header}\n\n{content}\n',
            'txt': f'{name}\n\n{header}\n\n{content}\n'}

class AudioError(ValueError):
    pass


def audio_duration(source):
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'default=noprint_wrappers=1:nokey=1', str(source)],
                               capture_output=True, text=True, check=True)
        try:
            duration = float(probe.stdout.strip())
        except ValueError:
            # MediaRecorder's streamed WebM may omit container duration metadata.
            # Measure the decoded end on the source timestamp timeline.
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(source),
                                      '-map', '0:a:0', '-f', 'null', '-', '-progress', 'pipe:1'],
                                     capture_output=True, text=True, check=True)
            times = re.findall(r'^out_time_us=(\d+)$', decoded.stdout, re.MULTILINE)
            duration = int(times[-1]) / 1_000_000 if times else 0
        if not 0 < duration < float('inf'):
            raise ValueError()
    except (OSError, subprocess.CalledProcessError, ValueError):
        raise AudioError('지원하지 않거나 손상된 음성 파일입니다.')
    return duration


def split_audio(source, directory):
    """Yield (start, end, PCM WAV path); all offsets are in source seconds."""
    duration = audio_duration(source)
    # PCM mono at 16 kHz fits well below 20 MB in 600 seconds, including its header.
    scan = subprocess.run(['ffmpeg', '-nostdin', '-i', str(source), '-af',
                           'silencedetect=noise=-35dB:d=0.4', '-f', 'null', '-'],
                          capture_output=True, text=True)
    if scan.returncode:
        raise AudioError('지원하지 않거나 손상된 음성 파일입니다.')
    silence = [float(t) for t in re.findall(r'silence_end: ([0-9.]+)', scan.stderr)]
    start = 0.0
    index = 0
    while start < duration:
        end = min(start + 600, duration)
        candidates = [t for t in silence if end - 30 <= t <= end and t > start + 2]
        if end < duration and candidates:
            end = candidates[-1]
        path = Path(directory) / f'chunk-{index}.wav'
        try:
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(start), '-i', str(source),
                            '-t', str(end - start), '-af', 'aresample=async=1:first_pts=0',
                            '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                            '-y', str(path)], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            raise AudioError('음성 파일을 분할할 수 없습니다.')
        if path.stat().st_size > 20_000_000:
            raise AudioError('분할된 음성 파일의 크기가 제한을 초과했습니다.')
        try:
            yield start, end, path
        finally:
            path.unlink(missing_ok=True)
        if end >= duration:
            break
        start = end - 2
        index += 1


def transcribe_file(source, name, imported_at, transcribe, checkpoint, cancelled=lambda: False, completed=None,
                    propagate_errors=False, partial_errors=(), partial_exclusions=(), temporary_root=None, recorded_at=None):
    """Transcribe in order; checkpoint each success; report failures as missing ranges.

    `transcribe` is the external API boundary and returns timed segments relative to
    the chunk. `checkpoint` receives completed chunks and merged segments.
    """
    import tempfile
    duration = audio_duration(source)
    completed = list(completed or [])
    segments, missing = [], []
    for chunk in completed:
        segments = merge_segments(segments, chunk['segments'], chunk['start'])
    import os
    with tempfile.TemporaryDirectory(dir=temporary_root, prefix=f'chunks-{os.getpid()}-') as tmp:
        failed = False
        partial_error = None
        for start, end, path in split_audio(source, tmp):
            if cancelled():
                return None
            if any(abs(chunk['start'] - start) < 0.001 and abs(chunk['end'] - end) < 0.001
                   for chunk in completed):
                continue
            if failed:
                missing.append((start, end))
                continue
            try:
                incoming = transcribe(path)
            except Exception as error:
                if cancelled():
                    return None
                if propagate_errors:
                    if not isinstance(error, partial_errors) or isinstance(error, partial_exclusions):
                        raise
                    partial_error = error
                    missing.append((start, audio_duration(source)))
                    break
                failed = True
                missing.append((start, end))
                continue
            if cancelled():
                return None
            segments = merge_segments(segments, incoming, start)
            completed.append({'start': start, 'end': end, 'segments': incoming})
            remaining = [(end, duration)] if end < duration else []
            checkpoint({'chunks': list(completed), 'segments': list(segments), 'missing': remaining,
                        'complete': not remaining, **render_transcript(name, imported_at, segments, remaining, recorded_at)})
    result = render_transcript(name, imported_at, segments, missing, recorded_at)
    if partial_error is not None:
        partial_error.partial_result = {**result, 'complete': False, 'segments': segments, 'missing': missing}
        raise partial_error
    return {**result, 'complete': not missing, 'segments': segments, 'missing': missing}

class GroqError(RuntimeError):
    def __init__(self, status, headers):
        super().__init__(f'Groq 요청 실패 (HTTP {status})')
        self.status = status
        self.headers = headers


class GroqClient:
    """One request per call, with no retries. cancel() interrupts an active socket."""
    def __init__(self, key, host='api.groq.com', port=None, secure=True):
        import threading
        self.key, self.host, self.port, self.secure = key, host, port, secure
        self.lock = threading.Lock()
        self.connection = None
        self.cancelled = False

    def cancel(self):
        import socket
        with self.lock:
            self.cancelled = True
            connection = self.connection
            if connection and connection.sock:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def transcribe(self, path, on_start=None):
        import http.client
        import uuid
        boundary = uuid.uuid4().hex
        fields = {'model': 'whisper-large-v3', 'language': 'ko', 'response_format': 'verbose_json'}
        body = b''
        for name, value in fields.items():
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                 'Content-Type: audio/wav\r\n\r\n').encode() + Path(path).read_bytes()
        body += f'\r\n--{boundary}--\r\n'.encode()
        cls = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
        connection = cls(self.host, self.port, timeout=120)
        with self.lock:
            if self.cancelled:
                raise RuntimeError('cancelled')
            self.connection = connection
        try:
            connection.connect()
            with self.lock:
                if self.cancelled:
                    raise RuntimeError('cancelled')
            connection.request('POST', '/openai/v1/audio/transcriptions', body, {
                'Authorization': f'Bearer {self.key}', 'Content-Type': f'multipart/form-data; boundary={boundary}'})
            if on_start:
                on_start()
            response = connection.getresponse()
            data = response.read()
            if response.status != 200:
                raise GroqError(response.status, dict(response.getheaders()))
            result = json.loads(data)
            return [{'start': float(s['start']), 'end': float(s['end']), 'text': s['text']}
                    for s in result['segments']]
        finally:
            with self.lock:
                self.connection = None
            connection.close()

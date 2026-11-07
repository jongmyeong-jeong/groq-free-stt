import unittest
from transcription import merge_segments, render_transcript


class TranscriptTest(unittest.TestCase):
    def test_overlap_preserves_original_time_and_removes_duplicate(self):
        previous = [{'start': 896, 'end': 899, 'text': '안녕하세요'}]
        result = merge_segments(previous, [{'start': 0, 'end': 1, 'text': '안녕하세요'},
                                           {'start': 2, 'end': 4, 'text': '다음 문장'}], 898)
        self.assertEqual(result, previous + [{'start': 900, 'end': 902, 'text': '다음 문장'}])

    def test_formats_include_same_segments_and_missing_range(self):
        result = render_transcript('음성.wav', '2026-11-07T10:00:00+09:00',
                                   [{'start': 1, 'end': 3, 'text': '안녕하세요'}],
                                   [(3, 8)])
        for value in result.values():
            self.assertIn('[00:00:01 - 00:00:03] 안녕하세요', value)
            self.assertIn('[00:00:03 - 00:00:08] 전사되지 않은 구간', value)
            self.assertIn('가져온 시각: 2026-11-07T10:00:00+09:00', value)
        self.assertNotIn('#', result['txt'])

    def test_recording_header_uses_recorded_start(self):
        result = render_transcript('recording', 'import-time', [], [], recorded_at='recorded-time')
        for value in result.values():
            self.assertIn('녹음 시작 시각: recorded-time', value)
            self.assertIn('녹음 시작 이후 경과 시간', value)
            self.assertNotIn('가져온 시각', value)

    def test_partial_overlap_removes_repeated_boundary_words(self):
        previous = [{'start': 896, 'end': 900, 'text': '오늘 날씨가 좋습니다'}]
        result = merge_segments(previous, [{'start': 0, 'end': 4, 'text': '날씨가 좋습니다 내일 만나요'}], 898)
        self.assertEqual(result[-1]['text'], '내일 만나요')

class AudioTest(unittest.TestCase):
    def test_streamed_webm_without_container_duration_is_transcribed(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from transcription import transcribe_file
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'recording.webm'
            with source.open('wb') as output:
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                                'sine=frequency=440:duration=2', '-c:a', 'libopus',
                                '-f', 'webm', 'pipe:1'], stdout=output, check=True)
            received = []
            def api(path):
                self.assertGreater(path.stat().st_size, 1000)
                received.append(path)
                return [{'start': 0, 'end': 1, 'text': 'hello'}]
            result = transcribe_file(source, 'recording.webm', 'now', api, lambda state: None)
            self.assertEqual(len(received), 1)
            self.assertTrue(result['complete'])
            self.assertEqual(result['segments'][0]['text'], 'hello')

    def test_streamed_webm_preserves_audio_after_timestamp_gap(self):
        import subprocess
        import tempfile
        import wave
        from pathlib import Path
        from transcription import transcribe_file
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'gap.webm'
            with source.open('wb') as output:
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                                'sine=frequency=440:duration=10', '-af',
                                r'asetpts=PTS+if(gte(T\,5)\,30/TB\,0)',
                                '-c:a', 'libopus', '-f', 'webm', 'pipe:1'],
                               stdout=output, check=True)
            def api(path):
                with wave.open(str(path)) as audio:
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 40, delta=0.1)
                    audio.setpos(37 * audio.getframerate())
                    self.assertNotEqual(set(audio.readframes(audio.getframerate())), {0})
                return [{'start': 37, 'end': 38, 'text': 'after gap'}]
            result = transcribe_file(source, 'gap.webm', 'now', api, lambda state: None)
            self.assertTrue(result['complete'])
            self.assertEqual(result['segments'][0]['start'], 37)

    def test_long_audio_splits_with_overlap_and_limits(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from transcription import split_audio
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '605', str(source)], check=True)
            chunks = []
            for start, end, path in split_audio(source, Path(tmp)):
                self.assertLessEqual(path.stat().st_size, 20_000_000)
                chunks.append((start, end, path))
            self.assertGreater(len(chunks), 1)
            self.assertEqual(chunks[0][0], 0)
            self.assertAlmostEqual(chunks[1][0], chunks[0][1] - 2, places=2)
            self.assertAlmostEqual(chunks[-1][1], 605, places=1)
            for start, end, path in chunks:
                self.assertLessEqual(end - start, 900)

    def test_processed_chunks_are_removed_before_next_request(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from transcription import transcribe_file
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '605', str(source)], check=True)
            seen = []
            def api(path):
                self.assertEqual(list(path.parent.glob('chunk-*.wav')), [path])
                seen.append(path)
                return []
            transcribe_file(source, 'source', 'now', api, lambda state: None)
            self.assertGreater(len(seen), 1)

    def test_checkpoint_contains_downloadable_partial_transcript(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from transcription import transcribe_file
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '605', str(source)], check=True)
            checkpoints = []
            transcribe_file(source, 'source', 'now', lambda path: [{'start': 1, 'end': 2, 'text': 'hello'}], checkpoints.append)
            self.assertIn('hello', checkpoints[0]['txt'])
            self.assertIn('[00:10:00 - 00:10:05] 전사되지 않은 구간', checkpoints[0]['md'])
            self.assertFalse(checkpoints[0]['complete'])

    def test_invalid_file_has_readable_error(self):
        import tempfile
        from pathlib import Path
        from transcription import split_audio, AudioError
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'bad.wav'
            source.write_bytes(b'not audio')
            with self.assertRaisesRegex(AudioError, '음성 파일'):
                list(split_audio(source, Path(tmp)))

    def test_partial_failure_is_checkpointed_and_exported(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from transcription import transcribe_file
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '605', str(source)], check=True)
            checkpoints = []
            def api(path):
                if checkpoints:
                    raise RuntimeError('offline')
                return [{'start': 1, 'end': 2, 'text': '첫 문장'}]
            result = transcribe_file(source, 'source.wav', 'import-time', api, checkpoints.append)
            self.assertEqual(len(checkpoints), 1)
            self.assertFalse(result['complete'])
            self.assertIn('첫 문장', result['txt'])
            self.assertIn('전사되지 않은 구간', result['md'])

    def test_resume_skips_successful_chunks(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from transcription import transcribe_file
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '605', str(source)], check=True)
            completed = [{'start': 0.0, 'end': 600.0, 'segments': [{'start': 1, 'end': 2, 'text': '첫 문장'}]}]
            calls = []
            def api(path):
                calls.append(path.name)
                return [{'start': 3, 'end': 4, 'text': '두 번째'}]
            result = transcribe_file(source, 'source.wav', 'import-time', api, lambda state: None,
                                     completed=completed)
            self.assertEqual(len(calls), 1)
            self.assertTrue(result['complete'])
            self.assertIn('[00:10:01 - 00:10:02] 두 번째', result['txt'])
            self.assertIn('첫 문장', result['txt'])

    def test_stop_discards_result_and_does_not_send_later_chunks(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from transcription import transcribe_file
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '605', str(source)], check=True)
            stopped = []
            checkpoints = []
            def api(path):
                stopped.append(True)
                return [{'start': 0, 'end': 1, 'text': 'discarded'}]
            result = transcribe_file(source, 'source.wav', 'import-time', api, checkpoints.append,
                                     cancelled=lambda: bool(stopped))
            self.assertIsNone(result)
            self.assertEqual(checkpoints, [])

class TransportTest(unittest.TestCase):
    def test_groq_request_uses_korean_whisper_and_timed_segments(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from transcription import GroqClient
        received = []
        class Response:
            status = 200
            def read(self):
                return b'{"segments":[{"start":0,"end":1,"text":"hello"}]}'
        class Connection:
            def __init__(self, *args, **kwargs):
                pass
            def connect(self):
                pass
            def request(self, method, path, body, headers):
                received.append(body)
            def getresponse(self):
                return Response()
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as tmp, patch('http.client.HTTPSConnection', Connection):
            audio = Path(tmp) / 'audio.wav'
            audio.write_bytes(b'audio')
            self.assertEqual(GroqClient('test-key').transcribe(audio)[0]['text'], 'hello')
        self.assertIn(b'whisper-large-v3', received[0])
        self.assertIn(b'\r\n\r\nko\r\n', received[0])
        self.assertIn(b'verbose_json', received[0])

    def test_cancel_during_connect_prevents_request(self):
        import tempfile
        import threading
        from pathlib import Path
        from unittest.mock import patch
        from transcription import GroqClient
        connecting, release, sent = threading.Event(), threading.Event(), []
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs):
                pass
            def connect(self):
                connecting.set()
                release.wait(3)
            def request(self, *args):
                sent.append(True)
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as tmp, patch('http.client.HTTPSConnection', Connection):
            audio = Path(tmp) / 'audio.wav'
            audio.write_bytes(b'audio')
            client = GroqClient('test')
            errors = []
            def send():
                try:
                    client.transcribe(audio)
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=send)
            worker.start()
            self.assertTrue(connecting.wait(3))
            client.cancel()
            release.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertTrue(errors)
            self.assertEqual(sent, [])

class JobTest(unittest.TestCase):
    def test_delete_and_new_worker_wait_for_stopped_worker_to_exit(self):
        import sqlite3
        import subprocess
        import tempfile
        import threading
        from pathlib import Path
        from unittest.mock import patch
        import job_transcription
        entered, release = threading.Event(), threading.Event()
        class BlockingClient:
            cancelled = False
            def __init__(self, key):
                pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                entered.set()
                release.wait(3)
                return [{'start': 0, 'end': 1, 'text': 'discarded'}]
            def cancel(self):
                self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '2', str(audio)], check=True)
            db_path = Path(tmp) / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.executemany('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', [
                    ('1', 'audio.wav', audio.read_bytes(), 'transcribing', '[]', '{}', None),
                    ('2', 'audio.wav', audio.read_bytes(), 'transcribing', '[]', '{}', None)])
            with patch.object(job_transcription, 'GroqClient', BlockingClient):
                job_transcription.start(db_path, '1', 'test')
                try:
                    self.assertTrue(entered.wait(3))
                    with sqlite3.connect(db_path) as db:
                        db.execute("UPDATE jobs SET status = 'stopped' WHERE id = '1'")
                    job_transcription.stop('1')
                    self.assertFalse(job_transcription.delete(db_path, '1'))
                    self.assertFalse(job_transcription.delete(db_path, '1', source_only=True))
                    with self.assertRaises(ValueError):
                        job_transcription.start(db_path, '2', 'test')
                finally:
                    release.set()
                    for _ in range(100):
                        if job_transcription.delete(db_path, '1'):
                            break
                        threading.Event().wait(.02)
                    else:
                        self.fail('stopped worker did not exit')

    def test_paused_job_exposes_checkpoint_documents(self):
        import json
        import sqlite3
        import subprocess
        import tempfile
        import threading
        import time
        from pathlib import Path
        from unittest.mock import patch
        import job_transcription
        entered, release = threading.Event(), threading.Event()
        class FakeClient:
            cancelled = False
            def __init__(self, key): pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                entered.set()
                release.wait(3)
                return [{'start': 1, 'end': 2, 'text': 'saved'}]
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '605', str(audio)], check=True)
            db_path = Path(tmp) / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('1', 'audio.wav', None, 'transcribing', '[]', '{}', str(audio)))
            with patch.object(job_transcription, 'GroqClient', FakeClient):
                job_transcription.start(db_path, '1', 'test')
                self.assertTrue(entered.wait(3))
                job_transcription.pause(db_path, '1')
                release.set()
                for _ in range(150):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'paused': break
                    time.sleep(.02)
            self.assertEqual(status, 'paused')
            result = json.loads(result)
            self.assertIn('saved', result['txt'])
            self.assertIn('전사되지 않은 구간', result['md'])
            self.assertFalse(result['complete'])

    def test_worker_saves_completed_result_to_job(self):
        import json
        import sqlite3
        import subprocess
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch
        import job_transcription
        class FakeClient:
            cancelled = False
            def __init__(self, key):
                pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                return [{'start': 0, 'end': 1, 'text': '반갑습니다'}]
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                            '-t', '2', str(audio)], check=True)
            db_path = Path(tmp) / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('1', 'audio.wav', audio.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', FakeClient):
                job_transcription.start(db_path, '1', 'test')
                for _ in range(100):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'completed':
                        break
                    time.sleep(.02)
            self.assertEqual(status, 'completed')
            self.assertIn('반갑습니다', json.loads(result)['txt'])

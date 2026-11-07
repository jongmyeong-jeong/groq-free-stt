import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import job_transcription
from transcription import GroqError


class WorkerQuotaTest(unittest.TestCase):
    def test_429_persists_wait_and_does_not_send_again(self):
        class Client:
            cancelled = False
            calls = 0
            def __init__(self, key): pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                Client.calls += 1
                raise GroqError(429, {'Retry-After': '3600'})
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '1', str(source)], check=True)
            db_path = root / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('1', 'audio.wav', source.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', Client):
                job_transcription.start(db_path, '1', 'test')
                for _ in range(200):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'waiting_limit': break
                    time.sleep(.01)
                self.assertEqual(status, 'waiting_limit')
                self.assertGreater(json.loads(result)['wait_until'], time.time())
                self.assertEqual(Client.calls, 1)
                job_transcription.stop('1')
                time.sleep(.3)

    def test_pause_waits_for_current_chunk_and_keeps_checkpoint(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        class Client:
            cancelled = False
            calls = 0
            def __init__(self, key): pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                Client.calls += 1
                entered.set()
                release.wait(5)
                return [{'start': 0, 'end': .5, 'text': 'kept'}]
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '605', str(source)], check=True)
            db_path = root / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('2', 'audio.wav', source.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', Client):
                job_transcription.start(db_path, '2', 'test')
                self.assertTrue(entered.wait(3))
                job_transcription.pause(db_path, '2')
                with sqlite3.connect(db_path) as db:
                    status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                self.assertEqual(status, 'transcribing')
                self.assertTrue(json.loads(result)['pause_requested'])
                release.set()
                for _ in range(200):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'paused': break
                    time.sleep(.01)
                self.assertEqual(status, 'paused')
                self.assertEqual(json.loads(result)['chunks'][0]['segments'][0]['text'], 'kept')
                self.assertNotIn('pause_requested', json.loads(result))
                self.assertEqual(Client.calls, 1)

    def test_authentication_failure_resumes_on_restart_without_losing_usage(self):
        class Client:
            cancelled = False
            calls = 0
            def __init__(self, key): self.key = key
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                Client.calls += 1
                if self.key == 'old': raise GroqError(401, {})
                return [{'start': 0, 'end': .5, 'text': 'resumed'}]
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '1', str(source)], check=True)
            db_path = root / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('3', 'audio.wav', source.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', Client):
                job_transcription.start(db_path, '3', 'old')
                for _ in range(200):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'paused': break
                    time.sleep(.01)
                self.assertEqual(json.loads(result)['error_code'], 'authentication')
                time.sleep(.05)
                job_transcription.recover(db_path, 'new')
                for _ in range(200):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'completed': break
                    time.sleep(.01)
                self.assertEqual(status, 'completed')
                self.assertIn('resumed', json.loads(result)['txt'])
                self.assertEqual(Client.calls, 2)
                self.assertEqual(len(json.loads((root / 'quota.json').read_text())), 2)

    def test_pause_cannot_revive_a_stop_committed_while_pause_waits_for_database(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, status TEXT, result TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?)', ('4', 'transcribing', '{}'))
            blocker = sqlite3.connect(db_path)
            blocker.execute('BEGIN IMMEDIATE')
            blocker.execute("UPDATE jobs SET status = 'stopped' WHERE id = '4'")
            started = threading.Event()
            errors = []
            def pause():
                started.set()
                try:
                    job_transcription.pause(db_path, '4')
                except ValueError as error:
                    errors.append(error)
            thread = threading.Thread(target=pause)
            thread.start()
            self.assertTrue(started.wait(1))
            blocker.commit()
            blocker.close()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            with sqlite3.connect(db_path) as db:
                self.assertEqual(db.execute('SELECT status FROM jobs').fetchone()[0], 'stopped')

    def test_pause_waits_for_admitted_request_to_start(self):
        import threading
        entering, allow_start, in_request, release = (threading.Event() for _ in range(4))
        class Client:
            cancelled = False
            def __init__(self, key): pass
            def transcribe(self, path, on_start=None):
                entering.set()
                allow_start.wait(3)
                on_start()
                in_request.set()
                release.wait(3)
                return [{'start': 0, 'end': .5, 'text': 'kept'}]
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '1', str(source)], check=True)
            db_path = root / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('5', 'audio.wav', source.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', Client):
                job_transcription.start(db_path, '5', 'test')
                self.assertTrue(entering.wait(3))
                paused = threading.Event()
                def pause():
                    job_transcription.pause(db_path, '5')
                    paused.set()
                thread = threading.Thread(target=pause)
                thread.start()
                self.assertFalse(paused.wait(.05))
                allow_start.set()
                self.assertTrue(in_request.wait(3))
                self.assertTrue(paused.wait(3))
                release.set()
                thread.join(3)
                for _ in range(200):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'paused': break
                    time.sleep(.01)
                self.assertEqual(status, 'paused')
                self.assertEqual(json.loads(result)['chunks'][0]['segments'][0]['text'], 'kept')

    def test_authentication_error_provides_partial_transcript_and_missing_range(self):
        class Client:
            cancelled = False
            calls = 0
            def __init__(self, key): pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                Client.calls += 1
                if Client.calls == 1:
                    return [{'start': 1, 'end': 2, 'text': '보존된 내용'}]
                raise GroqError(401, {})
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '605', str(source)], check=True)
            db_path = root / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('6', 'audio.wav', source.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', Client):
                job_transcription.start(db_path, '6', 'old')
                for _ in range(300):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'paused': break
                    time.sleep(.01)
                result = json.loads(result)
                self.assertEqual(status, 'paused')
                self.assertEqual(result['error_code'], 'authentication')
                for kind in ('md', 'txt'):
                    self.assertIn('보존된 내용', result[kind])
                    self.assertIn('전사되지 않은 구간', result[kind])
                self.assertFalse(result['complete'])
                self.assertEqual(Client.calls, 2)

    def test_startup_removes_only_owned_temporary_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            temp_root = root / 'transcription-tmp'
            temp_root.mkdir()
            stale = temp_root / 'job-99999999-abandoned'
            stale.mkdir()
            (stale / 'source').write_text('temporary')
            chunks = temp_root / 'chunks-99999999-abandoned'
            chunks.mkdir()
            (chunks / 'chunk.wav').write_text('temporary')
            active = temp_root / ('job-%d-active' % __import__('os').getpid())
            active.mkdir()
            unrelated = temp_root / 'keep'
            unrelated.mkdir()
            outside = root / 'outside'
            outside.mkdir()
            (outside / 'keep').write_text('safe')
            (temp_root / 'job-99999999-link').symlink_to(outside, target_is_directory=True)
            job_transcription.cleanup_temporary_files(root / 'jobs.sqlite3')
            self.assertFalse(stale.exists())
            self.assertFalse(chunks.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(active.exists())
            self.assertTrue((outside / 'keep').exists())

    def test_retry_exhaustion_preserves_partial_transcript(self):
        class Client:
            cancelled = False
            calls = 0
            def __init__(self, key): pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                Client.calls += 1
                if Client.calls == 1:
                    return [{'start': 1, 'end': 2, 'text': '이전 구간'}]
                raise OSError('offline')
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '605', str(source)], check=True)
            db_path = root / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('7', 'audio.wav', source.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', Client):
                job_transcription.start(db_path, '7', 'test')
                for _ in range(300):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'paused': break
                    time.sleep(.01)
                result = json.loads(result)
                self.assertEqual(status, 'paused')
                for kind in ('md', 'txt'):
                    self.assertIn('이전 구간', result[kind])
                    self.assertIn('전사되지 않은 구간', result[kind])
                self.assertFalse(result['complete'])
                self.assertEqual(Client.calls, 4)

    def test_unexpected_http_error_preserves_partial_transcript(self):
        class Client:
            cancelled = False
            calls = 0
            def __init__(self, key): pass
            def transcribe(self, path, on_start=None):
                if on_start: on_start()
                Client.calls += 1
                if Client.calls == 1:
                    return [{'start': 1, 'end': 2, 'text': '앞 구간'}]
                raise GroqError(400, {})
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '605', str(source)], check=True)
            db_path = root / 'jobs.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE jobs (id TEXT, name TEXT, source BLOB, status TEXT, segments TEXT, result TEXT, source_path TEXT)')
                db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)', ('8', 'audio.wav', source.read_bytes(), 'transcribing', '[]', '{}', None))
            with patch.object(job_transcription, 'GroqClient', Client):
                job_transcription.start(db_path, '8', 'test')
                for _ in range(300):
                    with sqlite3.connect(db_path) as db:
                        status, result = db.execute('SELECT status, result FROM jobs').fetchone()
                    if status == 'paused': break
                    time.sleep(.01)
                result = json.loads(result)
                self.assertEqual(status, 'paused')
                for kind in ('md', 'txt'):
                    self.assertIn('앞 구간', result[kind])
                    self.assertIn('전사되지 않은 구간', result[kind])
                self.assertFalse(result['complete'])
                self.assertEqual(Client.calls, 2)

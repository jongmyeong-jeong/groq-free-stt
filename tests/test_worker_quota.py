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
            def transcribe(self, path):
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
            def __init__(self, key): pass
            def transcribe(self, path):
                entered.set()
                release.wait(5)
                return [{'start': 0, 'end': .5, 'text': 'kept'}]
            def cancel(self): self.cancelled = True
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '1', str(source)], check=True)
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

    def test_authentication_failure_resumes_on_restart_without_losing_usage(self):
        class Client:
            cancelled = False
            calls = 0
            def __init__(self, key): self.key = key
            def transcribe(self, path):
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

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import job_transcription
import source_jobs
from recordings import RecordingStore


class SourceJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / 'jobs.sqlite3'
        with sqlite3.connect(self.db_path) as db:
            db.executescript('''CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT, source BLOB, source_path TEXT,
                status TEXT, segments TEXT DEFAULT '[]', usage TEXT DEFAULT '[]', result TEXT DEFAULT '{}');
                CREATE UNIQUE INDEX one_active ON jobs ((1)) WHERE status IN ('transcribing','paused','waiting_limit');''')

    def test_recording_delete_waits_for_copy_and_job_is_independent(self):
        store = RecordingStore(self.root)
        key = store.start('audio/webm')['id']
        store.append(key, 0, b'first')
        store.finish(key, 1)
        entered, release, deleted = threading.Event(), threading.Event(), threading.Event()
        original = source_jobs._copy_recording
        def copy(db, schema, recording_id, output):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(db, schema, recording_id, output)
        created = []
        def delete():
            with sqlite3.connect(store.path, timeout=5) as db:
                db.execute('DELETE FROM chunks WHERE recording_id = ?', (key,))
                db.execute('DELETE FROM recordings WHERE id = ?', (key,))
            deleted.set()
        with patch.object(source_jobs, '_copy_recording', side_effect=copy):
            copier = threading.Thread(target=lambda: created.append(source_jobs.from_recording(self.db_path, store.path, key, 'audio.webm')))
            copier.start()
            self.assertTrue(entered.wait(2))
            deleter = threading.Thread(target=delete)
            deleter.start()
            self.assertFalse(deleted.wait(.2))
            release.set()
            copier.join(5)
            deleter.join(5)
        self.assertEqual(len(created), 1)
        self.assertTrue(deleted.is_set())
        self.assertEqual((self.root / 'sources' / created[0]).read_bytes(), b'first')

    def test_retry_and_original_source_deletion_are_serialized(self):
        old = self.root / 'old'
        old.write_bytes(b'retained audio')
        with sqlite3.connect(self.db_path) as db:
            db.execute('INSERT INTO jobs (id, name, source_path, status, result) VALUES (?, ?, ?, ?, ?)',
                       ('old', 'audio.webm', str(old), 'stopped', json.dumps({'imported_at': 'original'})))
        entered, release, deleted = threading.Event(), threading.Event(), threading.Event()
        original = source_jobs.shutil.copyfileobj
        def copy(source, output, size):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(source, output, size)
        created = []
        def retry():
            created.append(job_transcription.retry(self.db_path, 'old'))
        def delete():
            self.assertTrue(job_transcription.delete(self.db_path, 'old', source_only=True))
            deleted.set()
        with patch.object(source_jobs.shutil, 'copyfileobj', side_effect=copy):
            copier = threading.Thread(target=retry)
            copier.start()
            self.assertTrue(entered.wait(2))
            deleter = threading.Thread(target=delete)
            deleter.start()
            self.assertFalse(deleted.wait(.2))
            release.set()
            copier.join(5)
            deleter.join(5)
        self.assertEqual(len(created), 1)
        self.assertTrue(deleted.is_set())
        self.assertEqual((self.root / 'sources' / created[0]).read_bytes(), b'retained audio')
        with sqlite3.connect(self.db_path) as db:
            result = db.execute('SELECT result FROM jobs WHERE id = ?', (created[0],)).fetchone()[0]
        self.assertEqual(json.loads(result)['imported_at'], 'original')

    def test_interrupted_copy_rolls_back_job_and_removes_partial_file(self):
        store = RecordingStore(self.root)
        key = store.start('audio/webm')['id']
        store.append(key, 0, b'audio')
        store.finish(key, 1)
        def fail(db, schema, recording_id, output):
            output.write(b'partial')
            raise OSError('disk full')
        with patch.object(source_jobs, '_copy_recording', side_effect=fail):
            with self.assertRaises(OSError):
                source_jobs.from_recording(self.db_path, store.path, key, 'audio.webm')
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
        self.assertEqual(list((self.root / 'sources').iterdir()), [])
        self.assertEqual(store.source(key), b'audio')

    def test_missing_decoder_leaves_recovery_pending(self):
        store = RecordingStore(self.root)
        key = store.start('audio/webm')['id']
        store.append(key, 0, b'audio')
        store.finish(key, 1, recover=True)
        with patch.object(source_jobs.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                source_jobs.check_recording(store, key)
        self.assertEqual(store.get(key)['status'], 'recovery_pending')
        self.assertEqual(store.source(key), b'audio')

    def test_only_stopped_job_can_be_retried(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO jobs (id, name, source, status) VALUES ('old', 'a', X'61', 'paused')")
        with self.assertRaises(ValueError):
            source_jobs.retry(self.db_path, 'old')
        self.assertEqual(list((self.root / 'sources').iterdir()), [])


if __name__ == '__main__':
    unittest.main()

"""Durable microphone recording fragments, independent of transcription jobs."""
import sqlite3
import time
import uuid
from pathlib import Path


class RecordingStore:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / 'recordings.sqlite3'
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, mime_type TEXT NOT NULL, started_at REAL NOT NULL,
                    elapsed REAL NOT NULL DEFAULT 0, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                    recording_id TEXT NOT NULL REFERENCES recordings(id),
                    sequence INTEGER NOT NULL, data BLOB NOT NULL,
                    PRIMARY KEY (recording_id, sequence));
            ''')
            db.execute("UPDATE recordings SET status = CASE WHEN EXISTS (SELECT 1 FROM chunks WHERE recording_id = recordings.id) THEN 'recovery_pending' ELSE 'recovery_failed' END WHERE status = 'recording'")
        self.path.chmod(0o600)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        return db

    def start(self, mime_type):
        if not isinstance(mime_type, str) or not mime_type.startswith('audio/'):
            raise ValueError('Invalid audio type')
        recording_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute('INSERT INTO recordings (id, mime_type, started_at, status) VALUES (?, ?, ?, ?)',
                       (recording_id, mime_type, time.time(), 'recording'))
        return self.get(recording_id)

    def get(self, recording_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM recordings WHERE id = ?', (recording_id,)).fetchone()
        if row is None:
            raise KeyError(recording_id)
        return dict(row)

    def list(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM recordings ORDER BY started_at DESC')]

    def append(self, recording_id, sequence, data, elapsed=0):
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0 or not isinstance(data, bytes) or not data:
            raise ValueError('Invalid chunk')
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not 0 <= elapsed < float('inf'):
            raise ValueError('Invalid elapsed time')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM recordings WHERE id = ?', (recording_id,)).fetchone()
            if row is None:
                raise KeyError(recording_id)
            if row['status'] != 'recording':
                raise ValueError('Recording is closed')
            existing = db.execute('SELECT data FROM chunks WHERE recording_id = ? AND sequence = ?', (recording_id, sequence)).fetchone()
            if existing:
                if existing['data'] != data:
                    raise ValueError('Conflicting chunk')
                return
            count = db.execute('SELECT COUNT(*) FROM chunks WHERE recording_id = ?', (recording_id,)).fetchone()[0]
            if sequence != count:
                raise ValueError('Out-of-order chunk')
            db.execute('INSERT INTO chunks VALUES (?, ?, ?)', (recording_id, sequence, data))
            db.execute('UPDATE recordings SET elapsed = MAX(elapsed, ?) WHERE id = ?', (elapsed, recording_id))

    def finish(self, recording_id, elapsed):
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not 0 <= elapsed < float('inf'):
            raise ValueError('Invalid elapsed time')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM recordings WHERE id = ?', (recording_id,)).fetchone()
            if row is None:
                raise KeyError(recording_id)
            if row['status'] != 'recording':
                return self.get(recording_id)
            count = db.execute('SELECT COUNT(*) FROM chunks WHERE recording_id = ?', (recording_id,)).fetchone()[0]
            db.execute('UPDATE recordings SET elapsed = ?, status = ? WHERE id = ?',
                       (elapsed, 'finished' if count else 'recovery_failed', recording_id))
        return self.get(recording_id)

    def source(self, recording_id):
        self.get(recording_id)
        with self._connect() as db:
            return b''.join(row['data'] for row in db.execute('SELECT data FROM chunks WHERE recording_id = ? ORDER BY sequence', (recording_id,)))

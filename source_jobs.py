"""Copy retained sources into independent jobs without loading entire audio in memory."""
import json
import os
import shutil
import subprocess
import tempfile
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BLOCK = 1024 * 1024


def _destination(db_path):
    directory = Path(db_path).parent / 'sources'
    directory.mkdir(mode=0o700, exist_ok=True)
    job_id = uuid.uuid4().hex
    return job_id, directory / job_id


def _copy_recording(db, schema, recording_id, output):
    lengths = db.execute(f'SELECT sequence, length(data) FROM {schema}.chunks WHERE recording_id = ? ORDER BY sequence', (recording_id,)).fetchall()
    if not lengths:
        raise ValueError('Recording is empty')
    for sequence, size in lengths:
        for offset in range(1, size + 1, BLOCK):
            data = db.execute(f'SELECT substr(data, ?, ?) FROM {schema}.chunks WHERE recording_id = ? AND sequence = ?',
                              (offset, BLOCK, recording_id, sequence)).fetchone()[0]
            output.write(data)


def check_recording(store, recording_id):
    # Hold a read snapshot while copying; the source remains intact if validation fails.
    with tempfile.TemporaryDirectory(dir=store.path.parent) as temp:
        path = Path(temp) / 'audio'
        with sqlite3.connect(store.path, timeout=10) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT status FROM recordings WHERE id = ?', (recording_id,)).fetchone()
            if not row or row[0] != 'recovery_pending':
                raise ValueError('Recording is not awaiting validation')
            with path.open('wb') as output:
                _copy_recording(db, 'main', recording_id, output)
        result = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(path),
                                 '-map', '0:a:0', '-f', 'null', '-'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return store.validate(recording_id, result.returncode == 0)


def from_recording(db_path, recording_path, recording_id, name):
    if not isinstance(name, str) or not name:
        raise ValueError('Invalid name')
    job_id, path = _destination(db_path)
    created = False
    try:
        with sqlite3.connect(db_path, timeout=10) as db:
            db.execute('ATTACH DATABASE ? AS recordings_db', (str(recording_path),))
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status, started_at FROM recordings_db.recordings WHERE id = ?', (recording_id,)).fetchone()
            if not row or row[0] not in ('finished', 'recovered'):
                raise ValueError('Recording is not available')
            # Reserve the single active slot before copying potentially large sources.
            imported = datetime.fromtimestamp(row[1], timezone.utc).isoformat()
            db.execute('INSERT INTO jobs (id, name, source_path, status, result) VALUES (?, ?, ?, ?, ?)',
                       (job_id, name, str(path), 'transcribing', json.dumps({'imported_at': imported})))
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created = True
            with os.fdopen(fd, 'wb') as output:
                _copy_recording(db, 'recordings_db', recording_id, output)
        return job_id
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


def retry(db_path, old_id):
    job_id, path = _destination(db_path)
    created = False
    try:
        with sqlite3.connect(db_path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT name, source_path, length(source), status, result FROM jobs WHERE id = ?', (old_id,)).fetchone()
            if not row or row[3] != 'stopped' or (row[1] is None and row[2] is None):
                raise ValueError('Stopped job source is not available')
            db.execute('INSERT INTO jobs (id, name, source_path, status, result) VALUES (?, ?, ?, ?, ?)',
                       (job_id, row[0], str(path), 'transcribing', json.dumps({'imported_at': json.loads(row[4]).get('imported_at', datetime.now(timezone.utc).isoformat())})))
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created = True
            with os.fdopen(fd, 'wb') as output:
                if row[1] is not None:
                    with open(row[1], 'rb') as source:
                        shutil.copyfileobj(source, output, BLOCK)
                else:
                    for offset in range(1, row[2] + 1, BLOCK):
                        data = db.execute('SELECT substr(source, ?, ?) FROM jobs WHERE id = ?', (offset, BLOCK, old_id)).fetchone()[0]
                        output.write(data)
        return job_id
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise

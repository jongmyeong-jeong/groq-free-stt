"""Background transcription jobs backed by the application's SQLite store."""
import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from transcription import AudioError, GroqClient, transcribe_file

_workers = {}
_lock = threading.Lock()


def start(db_path, job_id, key):
    if not key:
        raise ValueError('Groq API 키를 .env에 입력하세요.')
    with _lock:
        if _workers:
            raise ValueError('이미 전사 중이거나 정지 처리 중입니다.')
        with sqlite3.connect(db_path) as db:
            row = db.execute('SELECT name, source, status, result, source_path FROM jobs WHERE id = ?', (job_id,)).fetchone()
            if not row or (row[1] is None and row[4] is None) or row[2] not in ('transcribing', 'paused'):
                raise ValueError('전사할 원본을 찾을 수 없습니다.')
            state = json.loads(row[3])
            state.setdefault('imported_at', datetime.now(timezone.utc).isoformat())
            db.execute("UPDATE jobs SET status = 'transcribing', result = ? WHERE id = ? AND status != 'stopped'", (json.dumps(state), job_id))
        client = GroqClient(key)
        _workers[job_id] = client
        thread = threading.Thread(target=_run, args=(db_path, job_id, row[0], row[1], row[4], state, client), daemon=True)
        thread.start()


def stop(job_id):
    with _lock:
        client = _workers.get(job_id)
        if client:
            client.cancel()


def delete(db_path, job_id, source_only=False):
    """Reject deletion while a job is unfinished or its worker is stopping."""
    with _lock, sqlite3.connect(db_path) as db:
        if job_id in _workers:
            return False
        row = db.execute('SELECT status, source_path FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if not row or row[0] in ('transcribing', 'paused', 'waiting_limit'):
            return False
        if source_only:
            db.execute('UPDATE jobs SET source = NULL, source_path = NULL WHERE id = ?', (job_id,))
        else:
            db.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
        db.commit()
        if row[1]:
            Path(row[1]).unlink(missing_ok=True)
        return True


def _run(db_path, job_id, name, source, source_path, state, client):
    def save(checkpoint):
        with sqlite3.connect(db_path) as db:
            state.update(checkpoint)
            db.execute("UPDATE jobs SET segments = ?, result = ? WHERE id = ? AND status != 'stopped'",
                       (json.dumps(checkpoint['segments']), json.dumps(state), job_id))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(source_path) if source_path else Path(tmp) / 'source'
            if not source_path:
                path.write_bytes(source)
            result = transcribe_file(path, name, state['imported_at'], client.transcribe, save,
                                     cancelled=lambda: client.cancelled, completed=state.get('chunks'))
        if result is not None:
            state.update(result)
            with sqlite3.connect(db_path) as db:
                db.execute("UPDATE jobs SET status = ?, segments = ?, result = ? WHERE id = ? AND status != 'stopped'",
                           ('completed' if result['complete'] else 'paused', json.dumps(result['segments']), json.dumps(state), job_id))
    except AudioError as error:
        state['error'] = str(error)
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE jobs SET status = 'paused', result = ? WHERE id = ? AND status != 'stopped'", (json.dumps(state), job_id))
    finally:
        with _lock:
            _workers.pop(job_id, None)

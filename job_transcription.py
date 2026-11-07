"""Background transcription jobs backed by the application's SQLite store."""
import json
import os
import sqlite3
import tempfile
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from transcription import AudioError, GroqClient, transcribe_file
from quota import (QuotaLedger, RetryPolicy, RequestController, WaitForLimit,
                   RetryExhausted, AuthenticationFailed, RequestCancelled)

_workers = {}
_lock = threading.Lock()
_admission = threading.Lock()


def start(db_path, job_id, key, manual=True):
    if not key:
        raise ValueError('Groq API 키를 .env에 입력하세요.')
    with _lock:
        if _workers:
            raise ValueError('이미 전사 중이거나 정지 처리 중입니다.')
        with sqlite3.connect(db_path) as db:
            row = db.execute('SELECT name, source, status, result, source_path FROM jobs WHERE id = ?', (job_id,)).fetchone()
            if not row or (row[1] is None and row[4] is None) or row[2] not in ('transcribing', 'paused', 'waiting_limit'):
                raise ValueError('전사할 원본을 찾을 수 없습니다.')
            state = json.loads(row[3])
            state.setdefault('imported_at', datetime.now(timezone.utc).isoformat())
            state.pop('pause_requested', None)
            state.pop('error', None)
            state.pop('error_code', None)
            db.execute("UPDATE jobs SET status = 'transcribing', result = ? WHERE id = ? AND status != 'stopped'", (json.dumps(state), job_id))
        client = GroqClient(key)
        if manual:
            RetryPolicy(Path(db_path).parent / 'retry.json').resume_after_stop()
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


class PauseRequested(Exception):
    pass


def pause(db_path, job_id):
    with _admission, _lock, sqlite3.connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status, result FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if not row or row[0] not in ('transcribing', 'waiting_limit'):
            raise ValueError('일시 정지할 전사가 없습니다.')
        state = json.loads(row[1])
        if job_id in _workers and row[0] == 'transcribing':
            state['pause_requested'] = True
            status = row[0]
        else:
            state.pop('pause_requested', None)
            status = 'paused'
        db.execute('UPDATE jobs SET status = ?, result = ? WHERE id = ?', (status, json.dumps(state), job_id))


def recover(db_path, key):
    if not key:
        return
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT id, status, result FROM jobs WHERE status IN ('transcribing', 'waiting_limit', 'paused')").fetchall()
    for job_id, status, result in rows:
        state = json.loads(result)
        if status in ('transcribing', 'waiting_limit') and not state.get('pause_requested') or (status == 'paused' and state.get('error_code') == 'authentication'):
            start(db_path, job_id, key, manual=False)
        elif state.get('pause_requested'):
            with sqlite3.connect(db_path) as db:
                state.pop('pause_requested', None)
                db.execute("UPDATE jobs SET status = 'paused', result = ? WHERE id = ?", (json.dumps(state), job_id))


def _run(db_path, job_id, name, source, source_path, state, client):
    directory = Path(db_path).parent
    controller = RequestController(client, QuotaLedger(directory / 'quota.json'),
                                   RetryPolicy(directory / 'retry.json'), lambda: client.cancelled)

    def check_pause():
        paused = False
        with sqlite3.connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status, result FROM jobs WHERE id = ?', (job_id,)).fetchone()
            if not row or row[0] == 'stopped' or client.cancelled:
                raise RequestCancelled()
            current = json.loads(row[1])
            if row[0] == 'paused' or current.get('pause_requested'):
                current.pop('pause_requested', None)
                db.execute("UPDATE jobs SET status = 'paused', result = ? WHERE id = ? AND status != 'stopped'", (json.dumps(current), job_id))
                paused = True
        if paused:
            raise PauseRequested()

    def save(checkpoint):
        with sqlite3.connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT result FROM jobs WHERE id = ?', (job_id,)).fetchone()
            if row is None:
                raise RequestCancelled()
            current = json.loads(row[0])
            state.update(current)
            state.update(checkpoint)
            db.execute("UPDATE jobs SET segments = ?, result = ? WHERE id = ? AND status != 'stopped'",
                       (json.dumps(checkpoint['segments']), json.dumps(state), job_id))
        check_pause()

    def send(path):
        with wave.open(str(path), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
        while True:
            def admit():
                _admission.acquire()
                try:
                    check_pause()
                except BaseException:
                    _admission.release()
                    raise
                released = False
                def release():
                    nonlocal released
                    if not released:
                        released = True
                        _admission.release()
                return release
            try:
                result = controller.send(path, duration, admit=admit)
                with sqlite3.connect(db_path) as db:
                    state.pop('wait_until', None)
                    db.execute("UPDATE jobs SET status = 'transcribing', result = ? WHERE id = ? AND status = 'waiting_limit'", (json.dumps(state), job_id))
                return result
            except WaitForLimit as wait:
                with sqlite3.connect(db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT status, result FROM jobs WHERE id = ?', (job_id,)).fetchone()
                    if not row or row[0] == 'stopped':
                        raise RequestCancelled()
                    current = json.loads(row[1])
                    current['wait_until'] = wait.until
                    db.execute("UPDATE jobs SET status = 'waiting_limit', result = ? WHERE id = ? AND status = 'transcribing'", (json.dumps(current), job_id))
                while time.time() < wait.until:
                    check_pause()
                    time.sleep(min(.25, max(0, wait.until - time.time())))

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(source_path) if source_path else Path(tmp) / 'source'
            if not source_path:
                path.write_bytes(source)
            result = transcribe_file(path, name, state['imported_at'], send, save,
                                     cancelled=lambda: client.cancelled, completed=state.get('chunks'), propagate_errors=True)
        if result is not None:
            state.update(result)
            state.pop('wait_until', None)
            with sqlite3.connect(db_path) as db:
                db.execute("UPDATE jobs SET status = ?, segments = ?, result = ? WHERE id = ? AND status != 'stopped'",
                           ('completed' if result['complete'] else 'paused', json.dumps(result['segments']), json.dumps(state), job_id))
    except (PauseRequested, RequestCancelled):
        pass
    except (AudioError, AuthenticationFailed, RetryExhausted) as error:
        state['error'] = ('Groq API 키를 확인하고 서버를 다시 시작하세요.' if isinstance(error, AuthenticationFailed)
                          else '요청을 재개하려면 확인이 필요합니다.' if isinstance(error, RetryExhausted) else str(error))
        if isinstance(error, AuthenticationFailed):
            state['error_code'] = 'authentication'
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE jobs SET status = 'paused', result = ? WHERE id = ? AND status != 'stopped'", (json.dumps(state), job_id))
    except Exception:
        state['error'] = '전사 중 오류가 발생했습니다.'
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE jobs SET status = 'paused', result = ? WHERE id = ? AND status != 'stopped'", (json.dumps(state), job_id))
    finally:
        with _lock:
            _workers.pop(job_id, None)

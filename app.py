"""Local persistence API for Groq Free STT. No external dependencies required."""
import base64
import binascii
import json
import os
import sqlite3
import threading
import time
import uuid
from urllib.parse import unquote
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import job_transcription
from recordings import RecordingStore

ROOT = Path(__file__).resolve().parent
DATA = Path.home() / '.groq-free-stt'
ACTIVE = ('transcribing', 'paused', 'waiting_limit')
STATUSES = (*ACTIVE, 'completed', 'stopped')
MAX_SOURCE_BYTES = 64_000_000
MAX_REQUEST_BYTES = 86_000_000


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() == 'GROQ_API_KEY':
                os.environ.setdefault('GROQ_API_KEY', value.strip().strip('"\''))


def connect():
    db = sqlite3.connect(DATA / 'jobs.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    DATA.chmod(0o700)
    fd = os.open(DATA / 'jobs.sqlite3', os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, source BLOB, status TEXT NOT NULL,
            segments TEXT NOT NULL DEFAULT '[]', usage TEXT NOT NULL DEFAULT '[]',
            result TEXT NOT NULL DEFAULT '{}')''')
        if 'source_path' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
            db.execute('ALTER TABLE jobs ADD COLUMN source_path TEXT')
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON jobs ((1)) WHERE status IN ('transcribing', 'paused', 'waiting_limit')")


def serialize(row):
    return {'id': row['id'], 'name': row['name'], 'status': row['status'],
            'has_source': bool(row['has_source']),
            **{key: json.loads(row[key]) for key in ('segments', 'usage', 'result')}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def reply(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self):
        port = self.server.server_port
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            return self.reply(403, {'error': 'Forbidden'})
        origin = self.headers.get('Origin')
        if origin is not None and origin != f'http://{host}':
            return self.reply(403, {'error': 'Forbidden'})
        streaming_upload = self.command == 'POST' and self.path == '/uploads'
        if self.command in ('POST', 'PATCH') and self.headers.get_content_type() != ('application/octet-stream' if streaming_upload else 'application/json'):
            return self.reply(403, {'error': 'Forbidden'})
        parts = self.path.strip('/').split('/')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if streaming_upload:
                name = unquote(self.headers.get('X-Filename', ''))
                if length <= 0 or not name:
                    return self.reply(400, {'error': '음성 파일과 이름을 입력하세요.'})
                job_id = uuid.uuid4().hex
                directory = DATA / 'sources'
                directory.mkdir(mode=0o700, exist_ok=True)
                path = directory / job_id
                try:
                    with path.open('xb') as output:
                        path.chmod(0o600)
                        remaining = length
                        while remaining:
                            block = self.rfile.read(min(remaining, 1024 * 1024))
                            if not block:
                                raise ValueError('업로드가 중단됐습니다.')
                            output.write(block)
                            remaining -= len(block)
                    with connect() as db:
                        db.execute('INSERT INTO jobs (id, name, source_path, status, result) VALUES (?, ?, ?, ?, ?)',
                                   (job_id, name, str(path), 'transcribing', json.dumps({'imported_at': datetime.now(timezone.utc).isoformat()})))
                        row = db.execute('SELECT id, name, status, (source IS NOT NULL OR source_path IS NOT NULL) AS has_source, segments, usage, result FROM jobs WHERE id = ?', (job_id,)).fetchone()
                    return self.reply(201, serialize(row))
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
            if length < 0 or length > MAX_REQUEST_BYTES:
                return self.reply(413, {'error': '파일이 너무 큽니다. 원본은 64 MB 이하여야 합니다.'})
            body = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body, dict):
                raise ValueError()
            if parts == ['recording-control'] and self.command == 'GET':
                return self.reply(200, {'stop': self.server.stopping.is_set()})
            if parts == ['shutdown'] and self.command == 'POST':
                with self.server.recording_start_lock:
                    self.server.stopping.set()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and any(item['status'] == 'recording' for item in self.server.recordings.list()):
                    time.sleep(.05)
                self.reply(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if parts[0] == 'recordings':
                store = self.server.recordings
                if parts == ['recordings']:
                    if self.command == 'GET':
                        return self.reply(200, store.list())
                    if self.command == 'POST':
                        with self.server.recording_start_lock:
                            if self.server.stopping.is_set():
                                return self.reply(409, {'error': 'Server is stopping'})
                            recording = store.start(body['mime_type'])
                        return self.reply(201, recording)
                if len(parts) == 3 and self.command == 'POST' and parts[2] == 'heartbeat':
                    store.heartbeat(parts[1])
                    return self.reply(200, {'ok': True})
                if len(parts) == 3 and self.command == 'POST' and parts[2] == 'chunks':
                    store.append(parts[1], body['sequence'], base64.b64decode(body['data'], validate=True), body['elapsed'])
                    return self.reply(200, {'ok': True})
                if len(parts) == 3 and self.command == 'POST' and parts[2] == 'finish':
                    return self.reply(200, store.finish(parts[1], body['elapsed'], body.get('recover', False)))
                if len(parts) == 3 and self.command == 'POST' and parts[2] == 'validate':
                    return self.reply(200, store.validate(parts[1], body['playable']))
                if len(parts) == 3 and self.command == 'GET' and parts[2] == 'source':
                    return self.reply(200, {'source': base64.b64encode(store.source(parts[1])).decode()})
                if len(parts) == 2 and self.command == 'GET':
                    return self.reply(200, store.get(parts[1]))
                return self.reply(404, {'error': 'Not found'})
            with connect() as db:
                if parts == ['jobs'] and self.command == 'POST':
                    name, encoded = body['name'], body['source']
                    if not isinstance(name, str) or not name or not isinstance(encoded, str):
                        raise ValueError()
                    if len(encoded) > ((MAX_SOURCE_BYTES + 2) // 3) * 4:
                        return self.reply(413, {'error': '파일이 너무 큽니다. 원본은 64 MB 이하여야 합니다.'})
                    source = base64.b64decode(encoded, validate=True)
                    if len(source) > MAX_SOURCE_BYTES:
                        return self.reply(413, {'error': '파일이 너무 큽니다. 원본은 64 MB 이하여야 합니다.'})
                    job_id = uuid.uuid4().hex
                    db.execute('INSERT INTO jobs (id, name, source, status, result) VALUES (?, ?, ?, ?, ?)',
                               (job_id, name, source, 'transcribing', json.dumps({'imported_at': datetime.now(timezone.utc).isoformat()})))
                    db.commit()
                    row = db.execute('SELECT id, name, status, (source IS NOT NULL OR source_path IS NOT NULL) AS has_source, segments, usage, result FROM jobs WHERE id = ?', (job_id,)).fetchone()
                    return self.reply(201, serialize(row))
                if parts == ['jobs'] and self.command == 'GET':
                    return self.reply(200, [serialize(row) for row in db.execute('SELECT id, name, status, (source IS NOT NULL OR source_path IS NOT NULL) AS has_source, segments, usage, result FROM jobs ORDER BY rowid DESC')])
                if len(parts) not in (2, 3) or parts[0] != 'jobs' or (len(parts) == 3 and parts[2] not in ('source', 'transcribe', 'md', 'txt')):

                    return self.reply(404, {'error': 'Not found'})
                row = db.execute('SELECT id, name, status, (source IS NOT NULL OR source_path IS NOT NULL) AS has_source, segments, usage, result FROM jobs WHERE id = ?', (parts[1],)).fetchone()
                if row is None:
                    return self.reply(404, {'error': 'Not found'})
                if len(parts) == 3 and parts[2] == 'transcribe' and self.command == 'POST':
                    try:
                        job_transcription.start(DATA / 'jobs.sqlite3', parts[1], os.environ.get('GROQ_API_KEY'))
                    except ValueError as error:
                        return self.reply(400, {'error': str(error)})
                    return self.reply(202, {'ok': True})
                if len(parts) == 3 and parts[2] in ('md', 'txt') and self.command == 'GET':
                    result = json.loads(row['result'])
                    if parts[2] not in result or row['status'] == 'stopped':
                        return self.reply(404, {'error': 'Result not found'})
                    data = result[parts[2]].encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Disposition', f'attachment; filename="transcript.{parts[2]}"')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if len(parts) == 3 and parts[2] == 'source':
                    if self.command == 'GET':
                        stored = db.execute('SELECT source, source_path FROM jobs WHERE id = ?', (parts[1],)).fetchone()
                        source = stored['source'] if stored['source'] is not None else (Path(stored['source_path']).read_bytes() if stored['source_path'] else None)
                        if source is None:
                            return self.reply(404, {'error': 'Source not found'})
                        return self.reply(200, {'source': base64.b64encode(source).decode()})
                    if self.command == 'DELETE':
                        if not job_transcription.delete(DATA / 'jobs.sqlite3', parts[1], source_only=True):
                            return self.reply(409, {'error': '전사를 정지하거나 완료한 후 삭제하세요.'})
                        return self.reply(200, {'ok': True})
                elif len(parts) == 3:
                    return self.reply(405, {'error': 'Method not allowed'})
                elif self.command == 'GET':
                    return self.reply(200, serialize(row))
                elif self.command == 'DELETE':
                    if not job_transcription.delete(DATA / 'jobs.sqlite3', parts[1]):
                        return self.reply(409, {'error': '전사를 정지하거나 완료한 후 삭제하세요.'})
                    return self.reply(200, {'ok': True})
                elif self.command == 'PATCH':
                    if set(body) - {'status', 'segments', 'usage', 'result'}:
                        raise ValueError()
                    if 'status' in body and body['status'] not in STATUSES:
                        raise ValueError()
                    for key, typ in (('segments', list), ('usage', list), ('result', dict)):
                        if key in body and not isinstance(body[key], typ):
                            raise ValueError()
                    if body.get('status') == 'paused':
                        if body != {'status': 'paused'}:
                            raise ValueError()
                        try:
                            job_transcription.pause(DATA / 'jobs.sqlite3', parts[1])
                        except ValueError as error:
                            return self.reply(409, {'error': str(error)})
                        return self.reply(200, serialize(db.execute('SELECT id, name, status, (source IS NOT NULL OR source_path IS NOT NULL) AS has_source, segments, usage, result FROM jobs WHERE id = ?', (parts[1],)).fetchone()))
                    if body.get('status') == 'stopped':
                        body['segments'] = []
                        body['result'] = {}
                        # Cancel before releasing the active-job database constraint.
                        job_transcription.stop(parts[1])
                    if body:
                        assignments = ', '.join(f'{key} = ?' for key in body)
                        values = [value if key == 'status' else json.dumps(value) for key, value in body.items()]
                        updated = db.execute(f"UPDATE jobs SET {assignments} WHERE id = ? AND status != 'stopped'",
                                             (*values, parts[1]))
                        if not updated.rowcount:
                            return self.reply(409, {'error': 'Job is stopped'})
                    elif row['status'] == 'stopped':
                        return self.reply(409, {'error': 'Job is stopped'})
                    db.commit()
                    return self.reply(200, serialize(db.execute('SELECT id, name, status, (source IS NOT NULL OR source_path IS NOT NULL) AS has_source, segments, usage, result FROM jobs WHERE id = ?', (parts[1],)).fetchone()))
                return self.reply(405, {'error': 'Method not allowed'})
        except sqlite3.IntegrityError:
            return self.reply(409, {'error': 'An unfinished transcription already exists'})
        except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
            return self.reply(400, {'error': 'Invalid request'})

    do_GET = do_POST = do_PATCH = do_DELETE = handle_request


if __name__ == '__main__':
    load_env()
    init()
    job_transcription.recover(DATA / 'jobs.sqlite3', os.environ.get('GROQ_API_KEY'))
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '8765'))), Handler)
    server.recordings = RecordingStore(DATA)
    server.stopping = threading.Event()
    server.recording_start_lock = threading.Lock()
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()

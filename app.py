"""Local persistence API for Groq Free STT. No external dependencies required."""
import base64
import binascii
import json
import os
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from recordings import RecordingStore

ROOT = Path(__file__).resolve().parent
DATA = Path.home() / '.groq-free-stt'
ACTIVE = ('transcribing', 'paused', 'waiting_limit')
STATUSES = (*ACTIVE, 'completed', 'stopped')


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
        if self.command in ('POST', 'PATCH') and self.headers.get_content_type() != 'application/json':
            return self.reply(403, {'error': 'Forbidden'})
        parts = self.path.strip('/').split('/')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body, dict):
                raise ValueError()
            if parts[0] == 'recordings':
                store = self.server.recordings
                if parts == ['recordings']:
                    if self.command == 'GET':
                        return self.reply(200, store.list())
                    if self.command == 'POST':
                        return self.reply(201, store.start(body['mime_type']))
                if len(parts) == 3 and self.command == 'POST' and parts[2] == 'chunks':
                    store.append(parts[1], body['sequence'], base64.b64decode(body['data'], validate=True), body['elapsed'])
                    return self.reply(200, {'ok': True})
                if len(parts) == 3 and self.command == 'POST' and parts[2] == 'finish':
                    return self.reply(200, store.finish(parts[1], body['elapsed']))
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
                    source = base64.b64decode(encoded, validate=True)
                    job_id = uuid.uuid4().hex
                    db.execute('INSERT INTO jobs (id, name, source, status) VALUES (?, ?, ?, ?)',
                               (job_id, name, source, 'transcribing'))
                    db.commit()
                    row = db.execute('SELECT id, name, status, source IS NOT NULL AS has_source, segments, usage, result FROM jobs WHERE id = ?', (job_id,)).fetchone()
                    return self.reply(201, serialize(row))
                if parts == ['jobs'] and self.command == 'GET':
                    return self.reply(200, [serialize(row) for row in db.execute('SELECT id, name, status, source IS NOT NULL AS has_source, segments, usage, result FROM jobs ORDER BY rowid DESC')])
                if len(parts) not in (2, 3) or parts[0] != 'jobs' or (len(parts) == 3 and parts[2] != 'source'):
                    return self.reply(404, {'error': 'Not found'})
                row = db.execute('SELECT id, name, status, source IS NOT NULL AS has_source, segments, usage, result FROM jobs WHERE id = ?', (parts[1],)).fetchone()
                if row is None:
                    return self.reply(404, {'error': 'Not found'})
                if len(parts) == 3:
                    if self.command == 'GET':
                        source = db.execute('SELECT source FROM jobs WHERE id = ?', (parts[1],)).fetchone()['source']
                        if source is None:
                            return self.reply(404, {'error': 'Source not found'})
                        return self.reply(200, {'source': base64.b64encode(source).decode()})
                    if self.command == 'DELETE':
                        db.execute('UPDATE jobs SET source = NULL WHERE id = ?', (parts[1],))
                        db.commit()
                        return self.reply(200, {'ok': True})
                elif self.command == 'GET':
                    return self.reply(200, serialize(row))
                elif self.command == 'DELETE':
                    db.execute('DELETE FROM jobs WHERE id = ?', (parts[1],))
                    db.commit()
                    return self.reply(200, {'ok': True})
                elif self.command == 'PATCH':
                    if set(body) - {'status', 'segments', 'usage', 'result'}:
                        raise ValueError()
                    if 'status' in body and body['status'] not in STATUSES:
                        raise ValueError()
                    for key, typ in (('segments', list), ('usage', list), ('result', dict)):
                        if key in body and not isinstance(body[key], typ):
                            raise ValueError()
                    if body.get('status') == 'stopped':
                        body['segments'] = []
                        body['result'] = {}
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
                    return self.reply(200, serialize(db.execute('SELECT id, name, status, source IS NOT NULL AS has_source, segments, usage, result FROM jobs WHERE id = ?', (parts[1],)).fetchone()))
                return self.reply(405, {'error': 'Method not allowed'})
        except sqlite3.IntegrityError:
            return self.reply(409, {'error': 'An unfinished transcription already exists'})
        except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
            return self.reply(400, {'error': 'Invalid request'})

    do_GET = do_POST = do_PATCH = do_DELETE = handle_request


if __name__ == '__main__':
    load_env()
    init()
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '8765'))), Handler)
    server.recordings = RecordingStore(DATA)
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()

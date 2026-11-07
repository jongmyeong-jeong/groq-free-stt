import io
import sqlite3
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import app
from system_capture import SystemCapture


class DisconnectTest(unittest.TestCase):
    def test_committed_uploaded_job_keeps_source_when_reply_disconnects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(app, 'DATA', root):
                app.init()
                handler = object.__new__(app.Handler)
                handler.path, handler.command = '/uploads', 'POST'
                handler.server = SimpleNamespace(server_port=8765)
                handler.headers = Message()
                handler.headers['Host'] = '127.0.0.1:8765'
                handler.headers['Content-Type'] = 'application/octet-stream'
                handler.headers['Content-Length'] = '5'
                handler.headers['X-Filename'] = 'audio.wav'
                handler.rfile = io.BytesIO(b'audio')
                with mock.patch.object(handler, 'reply', side_effect=BrokenPipeError):
                    with self.assertRaises(BrokenPipeError):
                        handler.handle_request()
                with sqlite3.connect(root / 'jobs.sqlite3') as db:
                    source_path = db.execute('SELECT source_path FROM jobs').fetchone()[0]
                self.assertEqual(Path(source_path).read_bytes(), b'audio')

    def test_committed_recording_job_keeps_source_when_reply_disconnects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / 'recordings'
            recordings.mkdir()
            recording_id = 'a' * 32
            (recordings / (recording_id + '.pcm')).write_bytes(b'\x01\x00')
            with mock.patch.object(app, 'DATA', root), mock.patch.object(app, 'capture', SystemCapture(recordings, root / 'helper')):
                app.init()
                handler = object.__new__(app.Handler)
                handler.path = '/system-recordings/' + recording_id + '/create-job'
                handler.command = 'POST'
                handler.server = SimpleNamespace(server_port=8765)
                handler.headers = Message()
                handler.headers['Host'] = '127.0.0.1:8765'
                handler.headers['Content-Type'] = 'application/json'
                handler.headers['Content-Length'] = '2'
                handler.rfile = io.BytesIO(b'{}')
                with mock.patch.object(handler, 'reply', side_effect=BrokenPipeError):
                    with self.assertRaises(BrokenPipeError):
                        handler.handle_request()
                with sqlite3.connect(root / 'jobs.sqlite3') as db:
                    source_path = db.execute('SELECT source_path FROM jobs').fetchone()[0]
                self.assertTrue(Path(source_path).read_bytes().startswith(b'RIFF'))

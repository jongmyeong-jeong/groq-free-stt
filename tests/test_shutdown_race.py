import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import Handler
from recordings import RecordingStore


class ShutdownRaceTest(unittest.TestCase):
    def test_shutdown_waits_for_a_recording_whose_start_is_in_flight(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RecordingStore(Path(temp))
            entered, release = threading.Event(), threading.Event()
            original_start = store.start

            def slow_start(mime_type):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timed out')
                return original_start(mime_type)

            store.start = slow_start
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.recordings = store
            server.stopping = threading.Event()
            server.recording_start_lock = threading.Lock()
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            url = f'http://127.0.0.1:{server.server_port}'

            def request(path, payload):
                req = urllib.request.Request(url + path, data=json.dumps(payload).encode(),
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)

            created, shutdown = [], []
            start_thread = threading.Thread(target=lambda: created.append(request('/recordings', {'mime_type': 'audio/webm'})))
            shutdown_thread = threading.Thread(target=lambda: shutdown.append(request('/shutdown', {})))
            try:
                start_thread.start()
                self.assertTrue(entered.wait(2))
                shutdown_thread.start()
                time.sleep(.2)
                self.assertTrue(shutdown_thread.is_alive(), 'shutdown must wait for in-flight start')
                release.set()
                start_thread.join(2)
                self.assertEqual(len(created), 1)
                request('/recordings/' + created[0]['id'] + '/finish', {'elapsed': 0, 'recover': True})
                shutdown_thread.join(3)
                self.assertEqual(shutdown, [{'ok': True}])
            finally:
                release.set()
                start_thread.join(3)
                shutdown_thread.join(3)
                server.shutdown()
                server.server_close()
                worker.join(3)


if __name__ == '__main__':
    unittest.main()

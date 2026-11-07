import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / '.lonta').mkdir()
        (self.home / '.lonta' / 'keep').write_text('untouched')
        shutil.copyfile(ROOT / 'app.py', self.home / 'app.py')
        shutil.copyfile(ROOT / 'recordings.py', self.home / 'recordings.py')
        shutil.copytree(ROOT / 'web', self.home / 'web')
        (self.home / '.env').write_text('GROQ_API_KEY=secret-key\n')
        self.start()

    def start(self):
        env = os.environ.copy()
        env.pop('GROQ_API_KEY', None)
        env.update(HOME=str(self.home), PORT='0')
        self.proc = subprocess.Popen([sys.executable, '-u', str(self.home / 'app.py')], cwd=ROOT,
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        line = self.proc.stdout.readline().strip()
        self.assertTrue(line.startswith('http://127.0.0.1:'), line)
        self.url = line

    def stop(self):
        self.proc.terminate()
        output = self.proc.communicate(timeout=5)[0]
        self.assertNotIn('secret-key', output)

    def tearDown(self):
        if self.proc.poll() is None:
            self.stop()
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers=headers or {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                body = response.read()
                self.assertNotIn(b'secret-key', body)
                return response.status, json.loads(body)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_web_is_served_and_other_paths_are_not_files(self):
        with urllib.request.urlopen(self.url + '/') as response:
            self.assertIn(b'Groq Free STT', response.read())
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertEqual(self.request('GET', '/..%2Fapp.py')[0], 404)

    def test_job_title_can_be_changed_after_stopping(self):
        _, job = self.request('POST', '/jobs', {'name': 'old', 'source': 'YQ=='})
        self.request('PATCH', f"/jobs/{job['id']}", {'status': 'stopped'})
        status, renamed = self.request('PATCH', f"/jobs/{job['id']}", {'name': 'new'})
        self.assertEqual((status, renamed['name']), (200, 'new'))

    def test_shutdown_waits_for_recording_finish(self):
        import threading
        _, recording = self.request('POST', '/recordings', {'mime_type': 'audio/webm'})
        key = recording['id']
        self.request('POST', f'/recordings/{key}/chunks', {'sequence': 0, 'data': 'YQ==', 'elapsed': 1})
        result = []
        thread = threading.Thread(target=lambda: result.append(self.request('POST', '/shutdown', {})))
        thread.start()
        for _ in range(50):
            if self.request('GET', '/recording-control')[1]['stop']:
                break
            time.sleep(.02)
        self.assertTrue(thread.is_alive())
        self.assertEqual(self.request('POST', f'/recordings/{key}/finish', {'elapsed': 2, 'recover': True})[0], 200)
        self.assertEqual(self.request('GET', f'/recordings/{key}')[1]['status'], 'recovery_pending')
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][0], 200)
        self.proc.communicate(timeout=3)

    def test_recording_chunks_survive_restart_without_creating_a_job(self):
        status, recording = self.request('POST', '/recordings', {'mime_type': 'audio/webm'})
        self.assertEqual(status, 201)
        key = recording['id']
        self.assertEqual(self.request('POST', f'/recordings/{key}/chunks', {
            'sequence': 0, 'data': base64.b64encode(b'fragment').decode(), 'elapsed': 3
        })[0], 200)
        self.stop()
        self.start()
        _, recordings = self.request('GET', '/recordings')
        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0]['status'], 'recovery_pending')
        self.assertEqual(recordings[0]['elapsed'], 3)
        self.assertEqual(self.request('GET', '/jobs')[1], [])
        self.assertEqual(self.request('GET', f'/recordings/{key}/source')[1]['source'], base64.b64encode(b'fragment').decode())

    def test_data_survives_restart_and_source_can_be_deleted_separately(self):
        status, job = self.request('POST', '/jobs', {'name': 'audio.wav', 'source': base64.b64encode(b'audio').decode()})
        self.assertEqual(status, 201)
        job_id = job['id']
        self.assertEqual(self.request('PATCH', f'/jobs/{job_id}', {
            'status': 'waiting_limit', 'segments': [{'text': 'hello', 'start': 0}],
            'usage': [{'seconds': 5}], 'result': {'txt': 'hello', 'md': '# hello'}
        })[0], 200)
        self.stop()
        self.start()
        _, restored = self.request('GET', f'/jobs/{job_id}')
        self.assertEqual(restored['status'], 'waiting_limit')
        self.assertEqual(restored['segments'], [{'text': 'hello', 'start': 0}])
        self.assertEqual(restored['usage'], [{'seconds': 5}])
        self.assertEqual(restored['result']['txt'], 'hello')
        self.assertEqual(self.request('GET', f'/jobs/{job_id}/source')[1]['source'], base64.b64encode(b'audio').decode())
        self.assertEqual(self.request('DELETE', f'/jobs/{job_id}/source')[0], 200)
        _, after = self.request('GET', f'/jobs/{job_id}')
        self.assertFalse(after['has_source'])
        self.assertEqual(after['result']['txt'], 'hello')
        self.assertEqual(self.request('DELETE', f'/jobs/{job_id}')[0], 200)
        self.assertEqual(self.request('GET', f'/jobs/{job_id}')[0], 404)
        self.assertEqual((self.home / '.lonta' / 'keep').read_text(), 'untouched')

    def test_only_one_unfinished_job_and_stop_discards_partial_transcript(self):
        payload = {'name': 'audio.wav', 'source': base64.b64encode(b'audio').decode()}
        _, first = self.request('POST', '/jobs', payload)
        job_id = first['id']
        for status in ('transcribing', 'paused', 'waiting_limit'):
            self.assertEqual(self.request('PATCH', f'/jobs/{job_id}', {'status': status})[0], 200)
            self.assertEqual(self.request('POST', '/jobs', payload)[0], 409)
        self.request('PATCH', f'/jobs/{job_id}', {'segments': [{'text': 'partial'}], 'result': {'txt': 'partial'}})
        _, stopped = self.request('PATCH', f'/jobs/{job_id}', {'status': 'stopped'})
        self.assertEqual(stopped['segments'], [])
        self.assertEqual(stopped['result'], {})
        self.assertTrue(stopped['has_source'])
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 201)

    def test_rejects_cross_origin_and_untrusted_host(self):
        payload = {'name': 'audio', 'source': ''}
        self.assertEqual(self.request('POST', '/jobs', payload, {'Origin': 'https://evil.example', 'Content-Type': 'text/plain'})[0], 403)
        self.assertEqual(self.request('GET', '/jobs', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/jobs', headers={'Origin': self.url})[0], 200)
        self.assertEqual(self.request('GET', '/jobs')[1], [])

    def test_stopped_job_rejects_late_updates(self):
        _, job = self.request('POST', '/jobs', {'name': 'audio', 'source': ''})
        path = '/jobs/' + job['id']
        self.request('PATCH', path, {'status': 'stopped'})
        self.assertEqual(self.request('PATCH', path, {'status': 'completed', 'segments': [{'text': 'late'}], 'result': {'txt': 'late'}})[0], 409)
        _, stopped = self.request('GET', path)
        self.assertEqual((stopped['status'], stopped['segments'], stopped['result']), ('stopped', [], {}))

    def test_existing_data_directory_is_private(self):
        self.stop()
        data = self.home / '.groq-free-stt'
        data.chmod(0o755)
        (data / 'jobs.sqlite3').chmod(0o644)
        self.start()
        self.assertEqual(data.stat().st_mode & 0o777, 0o700)
        self.assertEqual((data / 'jobs.sqlite3').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()

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
        for filename in ('app.py', 'job_transcription.py', 'transcription.py', 'recordings.py'):
            shutil.copyfile(ROOT / filename, self.home / filename)
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

    def test_oversized_request_is_rejected_before_reading_body(self):
        req = urllib.request.Request(self.url + '/jobs', data=b'', method='POST', headers={
            'Content-Type': 'application/json', 'Content-Length': '86000001'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(caught.exception.code, 413)
        self.assertIn('64 MB', json.loads(caught.exception.read())['error'])

    def test_subresource_methods_cannot_mutate_job(self):
        _, job = self.request('POST', '/jobs', {'name': 'audio.wav', 'source': base64.b64encode(b'audio').decode()})
        for suffix in ('transcribe', 'md', 'txt'):
            self.assertEqual(self.request('DELETE', f"/jobs/{job['id']}/{suffix}")[0], 405)
            self.assertEqual(self.request('PATCH', f"/jobs/{job['id']}/{suffix}", {'status': 'stopped'})[0], 405)
        self.assertEqual(self.request('GET', f"/jobs/{job['id']}")[1]['status'], 'transcribing')

    def test_active_job_and_source_cannot_be_deleted(self):
        _, job = self.request('POST', '/jobs', {'name': 'audio.wav', 'source': base64.b64encode(b'audio').decode()})
        self.assertEqual(self.request('DELETE', f"/jobs/{job['id']}")[0], 409)
        self.assertEqual(self.request('DELETE', f"/jobs/{job['id']}/source")[0], 409)
        self.assertTrue(self.request('GET', f"/jobs/{job['id']}")[1]['has_source'])

    def test_large_upload_streams_and_can_be_deleted(self):
        import http.client
        from urllib.parse import urlsplit
        address = urlsplit(self.url)
        conn = http.client.HTTPConnection(address.hostname, address.port, timeout=15)
        size = 65_000_000
        conn.putrequest('POST', '/uploads')
        conn.putheader('Content-Type', 'application/octet-stream')
        conn.putheader('X-Filename', 'long.wav')
        conn.putheader('Content-Length', str(size))
        conn.endheaders()
        block = b'\0' * 1_000_000
        for _ in range(65):
            conn.send(block)
        response = conn.getresponse()
        self.assertEqual(response.status, 201)
        job = json.loads(response.read())
        conn.close()
        self.assertTrue(job['has_source'])
        self.assertEqual(self.request('PATCH', f"/jobs/{job['id']}", {'status': 'stopped'})[0], 200)
        self.assertEqual(self.request('DELETE', f"/jobs/{job['id']}/source")[0], 200)
        self.assertFalse(self.request('GET', f"/jobs/{job['id']}")[1]['has_source'])

    def test_invalid_audio_reports_failure_without_calling_groq(self):
        _, job = self.request('POST', '/jobs', {'name': 'bad.wav', 'source': base64.b64encode(b'bad').decode()})
        job_id = job['id']
        self.assertEqual(self.request('POST', f'/jobs/{job_id}/transcribe', {})[0], 202)
        for _ in range(100):
            _, job = self.request('GET', f'/jobs/{job_id}')
            if job['status'] == 'paused':
                break
            time.sleep(.02)
        self.assertEqual(job['status'], 'paused')
        self.assertIn('음성 파일', job['result']['error'])
        self.assertIn('imported_at', job['result'])
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
        self.assertEqual(self.request('PATCH', f'/jobs/{job_id}', {'status': 'completed'})[0], 200)
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

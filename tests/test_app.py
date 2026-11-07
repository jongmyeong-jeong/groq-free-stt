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

    def request(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                body = response.read()
                self.assertNotIn(b'secret-key', body)
                return response.status, json.loads(body)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

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


if __name__ == '__main__':
    unittest.main()

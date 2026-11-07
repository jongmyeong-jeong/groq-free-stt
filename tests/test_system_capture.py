import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from system_capture import SystemCapture


class SystemCaptureTest(unittest.TestCase):
    def test_stop_and_restart_recover_without_starting_a_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys\nf=open(sys.argv[1],"wb"); f.write(b"\\x01\\x00"); f.flush()\nprint("ready",flush=True)\nfor line in sys.stdin:\n if line.strip()=="stop": break\nf.close()\n')
            helper.chmod(0o700)
            capture = SystemCapture(root / 'recordings', helper)
            started = capture.start()
            self.assertEqual(started['status'], 'recording')
            self.assertEqual(capture.pause()['status'], 'paused')
            self.assertEqual(capture.resume()['status'], 'recording')
            capture.stop()
            self.assertEqual(SystemCapture(root / 'recordings', helper).recoveries(), [
                {'id': started['id'], 'status': 'recovered', 'source': started['id'] + '.wav'}])
            self.assertTrue((root / 'recordings' / (started['id'] + '.pcm')).exists())

    def test_missing_heartbeat_stops_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys\nf=open(sys.argv[1],"wb"); f.write(b"\\x01\\x00"); f.flush()\nprint("ready",flush=True)\nfor line in sys.stdin:\n if line.strip()=="stop": break\nf.close()\n')
            helper.chmod(0o700)
            capture = SystemCapture(root / 'recordings', helper, heartbeat_timeout=0.1)
            capture.start()
            deadline = time.monotonic() + 4
            while capture.status()['status'] != 'idle' and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(capture.status()['status'], 'idle')
            self.assertEqual(capture.recoveries()[0]['status'], 'recovered')

    def test_helper_receives_eof_when_server_disappears(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nprint("ready",flush=True)\nfor line in sys.stdin: pass\nPath(sys.argv[1]).with_suffix(".eof").write_text("closed")\n')
            helper.chmod(0o700)
            code = 'import os, sys; from system_capture import SystemCapture; c=SystemCapture(sys.argv[1],sys.argv[2]); c.start(); os._exit(0)'
            subprocess.run([sys.executable, '-c', code, str(root), str(helper)], check=True)
            deadline = time.monotonic() + 4
            while not list(root.glob('*.eof')) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(list(root.glob('*.eof'))), 1)

    def test_helper_failure_is_not_a_successful_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/bin/sh\nexit 1\n')
            helper.chmod(0o700)
            capture = SystemCapture(root / 'recordings', helper)
            with self.assertRaises(RuntimeError):
                capture.start()
            self.assertEqual(capture.status()['status'], 'idle')

import os
import fcntl
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from system_capture import SystemCapture


class SystemCaptureTest(unittest.TestCase):
    def wait_for(self, capture, state):
        deadline = time.monotonic() + 5
        while capture.status()['status'] != state and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(capture.status()['status'], state)

    def test_stop_and_restart_recover_without_starting_a_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys\nf=open(sys.argv[1],"wb"); f.write(b"\\x01\\x00"); f.flush()\nprint("ready",flush=True)\nfor line in sys.stdin:\n if line.strip()=="stop": break\n print("ack " + line.strip(),flush=True)\nf.close()\n')
            helper.chmod(0o700)
            capture = SystemCapture(root / 'recordings', helper)
            started = capture.start()
            self.assertEqual(started['status'], 'starting')
            self.wait_for(capture, 'recording')
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
            helper.write_text('#!/usr/bin/env python3\nimport sys\nf=open(sys.argv[1],"wb"); f.write(b"\\x01\\x00"); f.flush()\nprint("ready",flush=True)\nfor line in sys.stdin:\n if line.strip()=="stop": break\n print("ack " + line.strip(),flush=True)\nf.close()\n')
            helper.chmod(0o700)
            capture = SystemCapture(root / 'recordings', helper, heartbeat_timeout=0.1)
            capture.start()
            deadline = time.monotonic() + 4
            while capture.status()['status'] not in ('idle', 'failed') and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(capture.status()['status'], 'idle')
            self.assertEqual(capture.recoveries()[0]['status'], 'recovered')

    def test_helper_receives_eof_when_server_disappears(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nprint("ready",flush=True)\nfor line in sys.stdin: pass\nPath(sys.argv[1]).with_suffix(".eof").write_text("closed")\n')
            helper.chmod(0o700)
            code = 'import os, sys, time\nfrom system_capture import SystemCapture\nc=SystemCapture(sys.argv[1],sys.argv[2]); c.start()\nwhile c.status()["status"] == "starting": time.sleep(.01)\nassert c.status()["status"] == "recording"\nos._exit(0)'
            subprocess.run([sys.executable, '-c', code, str(root), str(helper)], check=True)
            deadline = time.monotonic() + 4
            while not list(root.glob('*.eof')) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(list(root.glob('*.eof'))), 1)

    def test_failed_helper_prefix_is_not_reported_as_recovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys\nf=open(sys.argv[1],"wb"); f.write(b"\\x01\\x00"); f.close()\nprint("ready",flush=True)\nsys.exit(1)\n')
            helper.chmod(0o700)
            capture = SystemCapture(root / 'recordings', helper)
            capture.start()
            deadline = time.monotonic() + 4
            while capture.status()['status'] not in ('idle', 'failed') and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(capture.recoveries()[0]['status'], 'failed')

    def test_permission_wait_is_async_and_control_waits_for_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys,time\ntime.sleep(.4)\nprint("ready",flush=True)\nfor line in sys.stdin:\n if line.strip()=="stop": break\n time.sleep(.2)\n print("ack " + line.strip(),flush=True)\n')
            helper.chmod(0o700)
            capture = SystemCapture(root, helper, heartbeat_timeout=3)
            self.assertEqual(capture.start()['status'], 'starting')
            time.sleep(.2)
            self.assertEqual(capture.heartbeat()['status'], 'starting')
            self.wait_for(capture, 'recording')
            before = time.monotonic()
            self.assertEqual(capture.pause()['status'], 'paused')
            self.assertGreaterEqual(time.monotonic() - before, .18)
            capture.stop()

    def test_long_permission_wait_stays_active_with_heartbeats(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys,time\ntime.sleep(1.4)\nprint("ready",flush=True)\nfor line in sys.stdin:\n if line.strip()=="stop": break\n')
            helper.chmod(0o700)
            capture = SystemCapture(root, helper, heartbeat_timeout=.15, startup_timeout=4)
            capture.start()
            deadline = time.monotonic() + 1.6
            while time.monotonic() < deadline:
                capture.heartbeat()
                time.sleep(.05)
            deadline = time.monotonic() + 2
            while capture.status()['status'] == 'starting' and time.monotonic() < deadline:
                capture.heartbeat()
                time.sleep(.05)
            self.assertEqual(capture.status()['status'], 'recording')
            capture.stop()

    def test_missing_ack_is_a_failure_not_a_pause(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/usr/bin/env python3\nimport sys\nprint("ready",flush=True)\nfor line in sys.stdin:\n if line.strip()=="stop": break\n')
            helper.chmod(0o700)
            capture = SystemCapture(root, helper, command_timeout=.1)
            capture.start()
            self.wait_for(capture, 'recording')
            with self.assertRaises(RuntimeError):
                capture.pause()
            self.assertEqual(capture.status()['status'], 'failed')

    def test_delete_rejects_a_recording_owned_by_another_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recording_id = 'a' * 32
            (root / (recording_id + '.pcm')).write_bytes(b'\x01\x00')
            capture = SystemCapture(root, root / 'helper')
            with (root / (recording_id + '.lock')).open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(RuntimeError):
                    capture.delete(recording_id)
                self.assertTrue((root / (recording_id + '.pcm')).exists())
            self.assertTrue(capture.delete(recording_id))
            self.assertFalse(capture.delete(recording_id))

    def test_helper_failure_is_not_a_successful_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / 'helper'
            helper.write_text('#!/bin/sh\nexit 1\n')
            helper.chmod(0o700)
            capture = SystemCapture(root / 'recordings', helper)
            capture.start()
            self.wait_for(capture, 'failed')

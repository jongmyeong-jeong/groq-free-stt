import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'requires macOS Swift compiler')
class NativeTimelineTest(unittest.TestCase):
    def test_buffer_timestamps_and_pause_survive_callback_delay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['swiftc', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'native' / 'SystemCapture.swift'),
                            str(ROOT / 'tests' / 'native' / 'main.swift'),
                            '-o', str(root / 'timeline-test')], check=True)
            subprocess.run([str(root / 'timeline-test'), str(root / 'audio.pcm')], check=True)
            owner = subprocess.Popen([str(root / 'timeline-test'), str(root / 'owner.pcm'), 'hold'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(owner.stdout.readline().strip(), 'locked')
                contender = subprocess.run([str(root / 'timeline-test'), str(root / 'other.pcm'), 'hold'],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.assertNotEqual(contender.returncode, 0)
                self.assertFalse((root / 'other.pcm').exists())
            finally:
                owner.stdin.write('stop\n')
                owner.stdin.flush()
                owner.stdin.close()
                owner.wait(timeout=5)
                owner.stdout.close()


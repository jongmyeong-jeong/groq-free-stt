import tempfile
import fcntl
import subprocess
import sys
import unittest
import wave
from pathlib import Path

from system_recovery import recover_recordings


class RecoveryTest(unittest.TestCase):
    def test_recovers_pcm_once_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / ('a' * 32 + '.pcm')
            original.write_bytes(b'\x01\x00\x02\x00')
            first = recover_recordings(root)
            self.assertEqual(first, [{'id': 'a' * 32, 'status': 'recovered', 'source': 'a' * 32 + '.wav'}])
            with wave.open(str(root / first[0]['source']), 'rb') as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 48000))
                self.assertEqual(audio.readframes(2), b'\x01\x00\x02\x00')
            self.assertEqual(recover_recordings(root), first)
            self.assertEqual(original.read_bytes(), b'\x01\x00\x02\x00')

    def test_existing_damaged_recovery_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / ('c' * 32 + '.pcm')
            original.write_bytes(b'\x01\x00\x02\x00')
            destination = root / ('c' * 32 + '.wav')
            with wave.open(str(destination), 'wb') as audio:
                audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x03\x00\x04\x00')
            self.assertEqual(recover_recordings(root), [{'id': 'c' * 32, 'status': 'failed', 'source': None}])
            self.assertTrue(destination.exists())
            self.assertEqual(original.read_bytes(), b'\x01\x00\x02\x00')

    def test_in_use_recording_is_not_recovered_until_owner_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recording_id = 'e' * 32
            (root / (recording_id + '.pcm')).write_bytes(b'\x01\x00')
            with (root / (recording_id + '.lock')).open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(recover_recordings(root), [{'id': recording_id, 'status': 'recording', 'source': None}])
                self.assertFalse((root / (recording_id + '.wav')).exists())
            self.assertEqual(recover_recordings(root)[0]['status'], 'recovered')

    def test_other_process_owns_recording_until_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recording_id = '9' * 32
            (root / (recording_id + '.pcm')).write_bytes(b'\x01\x00')
            code = 'import fcntl,sys; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read()'
            process = subprocess.Popen([sys.executable, '-c', code, str(root / (recording_id + '.lock'))],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'locked')
                self.assertEqual(recover_recordings(root)[0]['status'], 'recording')
                self.assertFalse((root / (recording_id + '.wav')).exists())
            finally:
                process.stdin.close()
                process.wait(timeout=5)
                process.stdout.close()
            self.assertEqual(recover_recordings(root)[0]['status'], 'recovered')

    def test_failed_capture_never_becomes_a_successful_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recording_id = 'f' * 32
            (root / (recording_id + '.pcm')).write_bytes(b'\x01\x00')
            (root / (recording_id + '.failed')).touch()
            self.assertEqual(recover_recordings(root), [{'id': recording_id, 'status': 'failed', 'source': recording_id + '.wav'}])
            self.assertTrue((root / (recording_id + '.wav')).exists())

    def test_invalid_source_is_retained_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / ('b' * 32 + '.pcm')
            original.write_bytes(b'\x01')
            self.assertEqual(recover_recordings(root), [{'id': 'b' * 32, 'status': 'failed', 'source': None}])
            self.assertEqual(original.read_bytes(), b'\x01')

import tempfile
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

    def test_invalid_source_is_retained_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / ('b' * 32 + '.pcm')
            original.write_bytes(b'\x01')
            self.assertEqual(recover_recordings(root), [{'id': 'b' * 32, 'status': 'failed', 'source': None}])
            self.assertEqual(original.read_bytes(), b'\x01')

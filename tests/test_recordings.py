import tempfile
import unittest
from pathlib import Path

from recordings import RecordingStore


class RecordingStoreTest(unittest.TestCase):
    def test_finish_keeps_elapsed_time_and_rejects_late_or_conflicting_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RecordingStore(Path(temp))
            recording = store.start('audio/webm')
            key = recording['id']
            store.append(key, 0, b'first')
            with self.assertRaises(ValueError):
                store.append(key, 0, b'different')
            with self.assertRaises(ValueError):
                store.append(key, 2, b'gap')
            self.assertEqual(store.finish(key, 12.5)['elapsed'], 12.5)
            with self.assertRaises(ValueError):
                store.append(key, 1, b'late')
            self.assertEqual(RecordingStore(Path(temp)).get(key)['status'], 'finished')

    def test_empty_interrupted_recording_is_a_retained_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            key = RecordingStore(Path(temp)).start('audio/webm')['id']
            store = RecordingStore(Path(temp))
            self.assertEqual(store.get(key)['status'], 'recovery_failed')
            self.assertEqual(store.source(key), b'')

    def test_failed_validation_keeps_source_and_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RecordingStore(Path(temp))
            key = store.start('audio/webm')['id']
            store.append(key, 0, b'broken')
            store = RecordingStore(Path(temp))
            self.assertEqual(store.validate(key, False)['status'], 'recovery_failed')
            self.assertEqual(store.source(key), b'broken')
            restored = RecordingStore(Path(temp))
            self.assertEqual(len(restored.list()), 1)
            self.assertEqual(restored.get(key)['status'], 'recovery_failed')

    def test_interrupted_recording_is_recovered_once_with_durable_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RecordingStore(Path(temp))
            recording = store.start('audio/webm')
            store.append(recording['id'], 0, b'first')
            store.append(recording['id'], 1, b'second')
            store.append(recording['id'], 1, b'second')
            recovered = RecordingStore(Path(temp))
            self.assertEqual(recovered.list()[0]['status'], 'recovery_pending')
            self.assertEqual(recovered.source(recording['id']), b'firstsecond')
            self.assertEqual(len(RecordingStore(Path(temp)).list()), 1)


if __name__ == '__main__':
    unittest.main()

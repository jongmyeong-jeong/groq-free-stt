import tempfile
import unittest
from pathlib import Path

from quota import RetryPolicy


class RetryPolicyTests(unittest.TestCase):
    def test_429_wait_and_consecutive_count_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'retry.json'
            policy = RetryPolicy(path, clock=lambda: 1000)
            self.assertEqual(policy.record_429('120'), ('wait', 1120))
            policy = RetryPolicy(path, clock=lambda: 1000)
            self.assertEqual(policy.current(), ('wait', 1120))
            self.assertEqual(policy.record_429(None), ('wait', 87400))
            self.assertEqual(policy.record_429('10'), ('stop', None))
            self.assertEqual(RetryPolicy(path, clock=lambda: 1000).current(), ('stop', None))

    def test_success_resets_429_count(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = RetryPolicy(Path(directory) / 'retry.json', clock=lambda: 1000)
            policy.record_429('0')
            policy.record_429('0')
            policy.record_success()
            self.assertEqual(policy.record_429('10'), ('wait', 1010))

    def test_transient_error_stops_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'retry.json'
            policy = RetryPolicy(path, clock=lambda: 1000)
            self.assertEqual(policy.record_transient(), ('ready', None))
            self.assertEqual(policy.record_transient(), ('ready', None))
            self.assertEqual(RetryPolicy(path).record_transient(), ('stop', None))

    def test_http_date_retry_after_and_invalid_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = RetryPolicy(Path(directory) / 'retry.json', clock=lambda: 1000)
            self.assertEqual(policy.record_429('Thu, 01 Jan 1970 00:20:00 GMT'), ('wait', 1200))
            policy.record_success()
            self.assertEqual(policy.record_429('not a date'), ('wait', 87400))

import tempfile
import unittest
from pathlib import Path

from quota import QuotaLedger, RetryPolicy, RequestController, AuthenticationFailed


class ResponseError(Exception):
    def __init__(self, status, headers=None):
        self.status, self.headers = status, headers or {}


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def transcribe(self, path):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class RequestControllerTests(unittest.TestCase):
    def test_auth_failure_stops_without_retry_and_keeps_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            client = Client([ResponseError(401)])
            ledger = QuotaLedger(path / 'quota', clock=lambda: 1000)
            policy = RetryPolicy(path / 'retry', clock=lambda: 1000)
            controller = RequestController(client, ledger, policy)
            with self.assertRaises(AuthenticationFailed):
                controller.send('audio', 900)
            self.assertEqual(client.calls, 1)
            restarted = QuotaLedger(path / 'quota', clock=lambda: 1000)
            for _ in range(7):
                self.assertIsNone(restarted.reserve(900))
            self.assertEqual(restarted.reserve(1), 4600)

    def test_429_wait_is_restored_before_any_new_request(self):
        from quota import WaitForLimit
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            now = [1000]
            clock = lambda: now[0]
            client = Client([ResponseError(429, {'Retry-After': '90'}), ['ok']])
            controller = RequestController(client, QuotaLedger(path / 'quota', clock), RetryPolicy(path / 'retry', clock))
            with self.assertRaises(WaitForLimit) as wait:
                controller.send('audio', 10)
            self.assertEqual(wait.exception.until, 1090)
            controller = RequestController(client, QuotaLedger(path / 'quota', clock), RetryPolicy(path / 'retry', clock))
            with self.assertRaises(WaitForLimit):
                controller.send('audio', 10)
            self.assertEqual(client.calls, 1)
            now[0] = 1090
            self.assertEqual(controller.send('audio', 10), ['ok'])

    def test_transient_errors_stop_after_three_recorded_attempts(self):
        from quota import RetryExhausted
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            client = Client([OSError('network')] * 3)
            controller = RequestController(client, QuotaLedger(path / 'quota', lambda: 1000), RetryPolicy(path / 'retry', lambda: 1000))
            with self.assertRaises(RetryExhausted):
                controller.send('audio', 10)
            self.assertEqual(client.calls, 3)

    def test_stop_during_request_never_retries_and_preserves_usage(self):
        from quota import RequestCancelled
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            stopped = [False]
            class StoppedClient:
                def transcribe(self, unused):
                    stopped[0] = True
                    raise OSError('interrupted')
            controller = RequestController(StoppedClient(), QuotaLedger(path / 'quota', lambda: 1000),
                                           RetryPolicy(path / 'retry', lambda: 1000), lambda: stopped[0])
            with self.assertRaises(RequestCancelled):
                controller.send('audio', 900)
            ledger = QuotaLedger(path / 'quota', lambda: 1000)
            for _ in range(7):
                self.assertIsNone(ledger.reserve(900))
            self.assertEqual(ledger.reserve(1), 4600)

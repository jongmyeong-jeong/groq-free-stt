import tempfile
import unittest
from pathlib import Path

from quota import QuotaLedger


class QuotaTests(unittest.TestCase):
    def test_request_is_reserved_before_sending_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quota.json'
            ledger = QuotaLedger(path, clock=lambda: 1000)
            self.assertIsNone(ledger.reserve(900))
            restarted = QuotaLedger(path, clock=lambda: 1000)
            for _ in range(7):
                self.assertIsNone(restarted.reserve(900))
            self.assertEqual(restarted.reserve(1), 4600)

    def test_request_rate_wait_expires_with_fake_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            ledger = QuotaLedger(Path(directory) / 'quota.json', clock=lambda: now[0])
            for _ in range(20):
                self.assertIsNone(ledger.reserve(1))
            self.assertEqual(ledger.reserve(1), 1060)
            now[0] = 1060
            self.assertIsNone(ledger.reserve(1))

    def test_daily_audio_budget_survives_hourly_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            ledger = QuotaLedger(Path(directory) / 'quota.json', clock=lambda: now[0])
            for _ in range(4):
                for _ in range(8):
                    self.assertIsNone(ledger.reserve(900))
                now[0] += 3600
            self.assertEqual(ledger.reserve(1), 87400)
            now[0] = 87400
            self.assertIsNone(ledger.reserve(1))

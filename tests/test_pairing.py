from __future__ import annotations

import time
import unittest

from cascadia.network.discovery import PairingManager, _PAIR_TTL_SECONDS


class PairingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pm = PairingManager()

    def test_generate_returns_6_digits(self) -> None:
        code = self.pm.generate_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertGreaterEqual(int(code), 100000)
        self.assertLessEqual(int(code), 999999)

    def test_valid_code_accepted(self) -> None:
        code = self.pm.generate_code()
        self.assertTrue(self.pm.validate_code(code))

    def test_used_code_rejected(self) -> None:
        code = self.pm.generate_code()
        self.pm.validate_code(code)       # consume it
        self.assertFalse(self.pm.validate_code(code))  # already used

    def test_invalid_code_rejected(self) -> None:
        self.assertFalse(self.pm.validate_code('000000'))
        self.assertFalse(self.pm.validate_code(''))

    def test_expired_code_rejected(self) -> None:
        code = self.pm.generate_code()
        # Backdating the created_at to make it expired
        with self.pm._lock:
            self.pm._codes[code]['created_at'] = time.time() - _PAIR_TTL_SECONDS - 1
        self.assertFalse(self.pm.validate_code(code))

    def test_pending_count_decrements_after_use(self) -> None:
        c1 = self.pm.generate_code()
        c2 = self.pm.generate_code()
        self.assertEqual(self.pm.pending_count(), 2)
        self.pm.validate_code(c1)
        self.assertEqual(self.pm.pending_count(), 1)

    def test_distinct_codes(self) -> None:
        codes = {self.pm.generate_code() for _ in range(10)}
        self.assertEqual(len(codes), 10)


if __name__ == '__main__':
    unittest.main()

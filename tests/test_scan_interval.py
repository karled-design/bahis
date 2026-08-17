from __future__ import annotations

import unittest

from config.settings import (
    MEASUREMENT_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    SCAN_INTERVAL_SECONDS,
    _resolve_scan_interval_env,
    resolve_scan_interval_seconds,
)


class ScanIntervalTests(unittest.TestCase):
    def test_measurement_mode_uses_fast_cadence(self) -> None:
        self.assertEqual(
            resolve_scan_interval_seconds(measurement_mode=True),
            min(SCAN_INTERVAL_SECONDS, MEASUREMENT_SCAN_INTERVAL_SECONDS),
        )

    def test_normal_mode_keeps_configured_interval(self) -> None:
        self.assertEqual(
            resolve_scan_interval_seconds(measurement_mode=False),
            SCAN_INTERVAL_SECONDS,
        )

    def test_env_override_is_floored(self) -> None:
        self.assertEqual(
            _resolve_scan_interval_env("5", default=600), MIN_SCAN_INTERVAL_SECONDS
        )
        self.assertEqual(_resolve_scan_interval_env("300", default=600), 300)

    def test_invalid_env_falls_back_to_default(self) -> None:
        self.assertEqual(_resolve_scan_interval_env("", default=600), 600)
        self.assertEqual(_resolve_scan_interval_env("abc", default=600), 600)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

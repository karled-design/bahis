from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from core import olcum_nabzi


class PulseScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_path = Path(tmp.name) / "olcum_nabiz_state.json"
        patcher = mock.patch.object(olcum_nabzi, "_STATE_PATH", self.state_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        enabled = mock.patch.object(olcum_nabzi, "is_measurement_mode_enabled", return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)
        olcum_nabzi._last_attempt_monotonic = 0.0
        olcum_nabzi._cycle_counter = 0

    def test_first_pulse_is_due(self) -> None:
        self.assertTrue(olcum_nabzi.is_pulse_due())

    def test_not_due_right_after_sending(self) -> None:
        olcum_nabzi.mark_pulse_sent()
        self.assertFalse(olcum_nabzi.is_pulse_due())
        self.assertTrue(self.state_path.is_file())

    def test_due_again_after_interval(self) -> None:
        now = olcum_nabzi._now_tr()
        olcum_nabzi.mark_pulse_sent(now=now)
        later = now + timedelta(minutes=olcum_nabzi.NABIZ_ARALIK_DAKIKA + 1)
        self.assertTrue(olcum_nabzi.is_pulse_due(now=later))

    def test_measurement_mode_off_never_due(self) -> None:
        with mock.patch.object(olcum_nabzi, "is_measurement_mode_enabled", return_value=False):
            self.assertFalse(olcum_nabzi.is_pulse_due())

    def test_corrupt_state_is_treated_as_due(self) -> None:
        self.state_path.write_text("{bozuk", encoding="utf-8")
        self.assertTrue(olcum_nabzi.is_pulse_due())


class PulseMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_path = Path(tmp.name) / "olcum_nabiz_state.json"
        for target, value in (
            ("_STATE_PATH", self.state_path),
        ):
            patcher = mock.patch.object(olcum_nabzi, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        enabled = mock.patch.object(olcum_nabzi, "is_measurement_mode_enabled", return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)
        olcum_nabzi._last_attempt_monotonic = 0.0
        olcum_nabzi._cycle_counter = 0
        olcum_nabzi._last_cycle_stats = {}

    def _patch_sources(self, *, measured: int) -> Any:
        card = {
            "toplam": {
                "sinyal": 12,
                "olculen": measured,
                "ort_clv_pct": 3.5,
                "pozitif_clv_orani": 66.7,
            },
            "kirilimlar": {},
        }
        credit = {"status_label": "Bugun 2/16 kredi | Kalan 498"}
        return (
            mock.patch.object(olcum_nabzi, "report", return_value=card),
            mock.patch.object(olcum_nabzi, "build_credit_panel_payload", return_value=credit),
        )

    def test_message_reports_cycles_funnel_and_scorecard(self) -> None:
        report_patch, credit_patch = self._patch_sources(measured=4)
        with report_patch, credit_patch:
            olcum_nabzi.record_cycle({"birlesik": 27, "pasif_ev": 23, "aday": 1, "izle": 1, "telegram": 1})
            olcum_nabzi.record_cycle({"birlesik": 30, "pasif_ev": 28, "aday": 2, "izle": 0, "telegram": 0})
            message = olcum_nabzi.build_pulse_message()

        self.assertIn("2 tarama turu", message)
        self.assertIn("30 mac karsilastirildi", message)
        self.assertIn("12 sinyal", message)
        self.assertIn("4 tanesi CLV ile olculdu", message)
        # 30'luk kanit esigine kalan sinyal sayisi acikca yazilmali.
        self.assertIn("26 olculmus sinyal daha", message)
        self.assertIn("Kalan 498", message)
        self.assertIn("gercek kupon acilmiyor", message)

    def test_scan_disabled_message_says_scan_off(self) -> None:
        report_patch, credit_patch = self._patch_sources(measured=0)
        with report_patch, credit_patch:
            message = olcum_nabzi.build_pulse_message(scan_enabled=False)
        self.assertIn("TARAMA KAPALI", message)
        self.assertNotIn("tarama turu", message)

    def test_proof_threshold_reached_message(self) -> None:
        report_patch, credit_patch = self._patch_sources(measured=30)
        with report_patch, credit_patch:
            message = olcum_nabzi.build_pulse_message()
        self.assertIn("Kanit esigi doldu", message)

    def test_send_marks_state_and_resets_counter(self) -> None:
        report_patch, credit_patch = self._patch_sources(measured=1)
        sent: list[str] = []
        with report_patch, credit_patch:
            olcum_nabzi.record_cycle({"birlesik": 5})
            ok = olcum_nabzi.maybe_send_measurement_pulse(lambda text: bool(sent.append(text)) or True)

        self.assertTrue(ok)
        self.assertEqual(len(sent), 1)
        self.assertEqual(olcum_nabzi._cycle_counter, 0)
        self.assertIn("sent_at", json.loads(self.state_path.read_text(encoding="utf-8")))

    def test_failed_send_does_not_mark_state(self) -> None:
        report_patch, credit_patch = self._patch_sources(measured=1)
        with report_patch, credit_patch:
            ok = olcum_nabzi.maybe_send_measurement_pulse(lambda _text: False)

        self.assertFalse(ok)
        self.assertFalse(self.state_path.exists())

    def test_message_build_failure_does_not_raise(self) -> None:
        with mock.patch.object(olcum_nabzi, "report", side_effect=RuntimeError("db yok")):
            ok = olcum_nabzi.maybe_send_measurement_pulse(lambda _text: True)
        self.assertFalse(ok)


class IntervalResolveTests(unittest.TestCase):
    def test_env_interval_has_floor(self) -> None:
        with mock.patch.dict("os.environ", {"OLCUM_NABIZ_DAKIKA": "1"}):
            self.assertEqual(olcum_nabzi._resolve_interval_minutes(), olcum_nabzi._MIN_INTERVAL_MINUTES)

    def test_invalid_env_falls_back(self) -> None:
        with mock.patch.dict("os.environ", {"OLCUM_NABIZ_DAKIKA": "abc"}):
            self.assertEqual(
                olcum_nabzi._resolve_interval_minutes(), olcum_nabzi._DEFAULT_INTERVAL_MINUTES
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

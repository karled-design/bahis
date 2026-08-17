from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from core.passion_engine import (
    calculate_expected_value,
    classify_ev_tier,
    get_active_min_ev_threshold,
)
from core.scan_pipeline import (
    count_legacy_action_candidates,
    count_current_work_orders,
    evaluate_matches,
)


class PassionEngineTests(unittest.TestCase):
    def test_calculate_expected_value(self) -> None:
        ev = calculate_expected_value(2.0, 2.10)
        self.assertAlmostEqual(ev, 0.05, places=4)

    def test_classify_watch_action_high(self) -> None:
        self.assertEqual(classify_ev_tier(0.02, consensus_books=2), "WATCH")
        self.assertEqual(classify_ev_tier(0.031, consensus_books=2), "ACTION")
        self.assertEqual(classify_ev_tier(0.06, consensus_books=2), "HIGH")

    def test_active_action_threshold(self) -> None:
        self.assertAlmostEqual(get_active_min_ev_threshold(), 0.03, places=4)


class ScanPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._hero_patch = mock.patch("core.scan_pipeline.is_hero_mode_enabled", return_value=False)
        self._hero_patch.start()

    def tearDown(self) -> None:
        self._hero_patch.stop()

    def _sample_matches(self) -> list[dict]:
        return [
            {
                "match_name": "Test A - Test B",
                "market": "MS1",
                "sharp_odds": 2.0,
                "soft_odds": 2.04,
                "consensus_books": 2,
                "consensus_source": "pinnacle",
            },
            {
                "match_name": "Test C - Test D",
                "market": "MS1",
                "sharp_odds": 2.0,
                "soft_odds": 2.06,
                "consensus_books": 2,
                "consensus_source": "pinnacle",
            },
            {
                "match_name": "Test E - Test F",
                "market": "MS1",
                "sharp_odds": 2.0,
                "soft_odds": 2.12,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
            },
        ]

    def test_evaluate_matches_tiers(self) -> None:
        candidates, stats = evaluate_matches(
            self._sample_matches(),
            current_kasa=1000.0,
            skip_freshness=True,
        )
        self.assertEqual(stats.watch, 1)
        self.assertEqual(stats.action, 1)
        self.assertEqual(stats.high, 1)
        self.assertEqual(stats.aday, 3)

    def test_legacy_vs_current_counts(self) -> None:
        matches = self._sample_matches()
        candidates, _ = evaluate_matches(matches, current_kasa=1000.0, skip_freshness=True)
        legacy = count_legacy_action_candidates(matches)
        current = count_current_work_orders(candidates)
        self.assertEqual(legacy, 2)
        self.assertEqual(current["total"], 3)
        self.assertEqual(current["watch"], 1)
        self.assertGreater(current["total"], legacy)


class TelegramFormatTests(unittest.TestCase):
    def test_empty_report_still_buildable_for_panel(self) -> None:
        from notifiers.telegram_worker import (
            _build_scan_empty_report_text,
            build_last_scan_panel_payload,
        )

        stats = {
            "birlesik": 10,
            "pasif_ev": 8,
            "tolerans": 0,
            "stale": 0,
            "efutbol": 0,
            "absurd_ev": 0,
            "watch": 2,
            "action": 0,
            "high": 0,
            "aday": 2,
            "telegram": 0,
            "izle": 2,
            "cooldown": 0,
        }
        text = _build_scan_empty_report_text(stats)
        self.assertIn("Funnel:", text)
        panel = build_last_scan_panel_payload(stats, scan_time="2026-06-19 12:00 UTC")
        self.assertEqual(panel["funnel"]["watch"], 2)
        self.assertIn("hint", panel)

    def test_empty_cycle_does_not_send_telegram_when_recommendations_only(self) -> None:
        from notifiers import telegram_worker

        self.assertTrue(telegram_worker.TELEGRAM_RECOMMENDATIONS_ONLY)
        telegram_worker.begin_scan_cycle("cycle-x")
        with mock.patch.object(telegram_worker, "_telegram_post", return_value=None) as post:
            sent = telegram_worker.send_scan_empty_report_for_cycle("cycle-x", None)
        self.assertTrue(sent)
        post.assert_not_called()

    def test_wait_for_scan_start_does_not_double_poll_get_updates(self) -> None:
        from notifiers import telegram_worker
        from ui.web_server import update_sistem_durumu

        update_sistem_durumu(scan_enabled=False)

        def _enable_scan_after_delay() -> None:
            time.sleep(0.15)
            update_sistem_durumu(scan_enabled=True)

        with mock.patch.object(telegram_worker, "start_telegram_listener") as start_listener:
            with mock.patch.object(telegram_worker, "_fetch_updates") as fetch_updates:
                threading.Thread(target=_enable_scan_after_delay, daemon=True).start()
                started = telegram_worker.wait_for_scan_start(poll_interval_seconds=0.05)

        self.assertTrue(started)
        start_listener.assert_called_once()
        fetch_updates.assert_not_called()

    def test_wait_for_scan_start_returns_false_on_timeout(self) -> None:
        from notifiers import telegram_worker
        from ui.web_server import update_sistem_durumu

        update_sistem_durumu(scan_enabled=False)
        with mock.patch.object(telegram_worker, "start_telegram_listener"):
            started = telegram_worker.wait_for_scan_start(
                poll_interval_seconds=0.01, timeout_seconds=0.05
            )
        self.assertFalse(started)

    def test_repeated_wait_calls_do_not_reset_polling_session(self) -> None:
        from notifiers import telegram_worker
        from ui.web_server import update_sistem_durumu

        update_sistem_durumu(scan_enabled=False)
        with mock.patch.object(telegram_worker, "_prepare_polling_session") as prepare:
            with mock.patch.object(telegram_worker, "start_telegram_listener"):
                for _ in range(3):
                    telegram_worker.wait_for_scan_start(
                        poll_interval_seconds=0.01, timeout_seconds=0.02
                    )
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()

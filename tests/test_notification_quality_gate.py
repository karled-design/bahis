from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from core.match_filters import SCAN_CYCLE
from core.notification_quality_gate import (
    evaluate_notification_quality,
    load_notification_gate_cases,
    run_notification_gate_theory_suite,
)
from core.scan_league_settings import update_scan_league_settings
from tests.live_state_isolation import isolate_module

isolate_module(globals())  # canli database dosyalari yerine gecici klasor

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "theory" / "notification_gate_cases.json"
_REFERENCE_AT = time.time()


def _future_match(**overrides: object) -> dict:
    ref = _REFERENCE_AT
    base = {
        "event_id": "ng-test-001",
        "sport_key": "soccer_fifa_world_cup",
        "match_name": "Brazil - Morocco",
        "league_name": "FIFA World Cup",
        "match_quality": "ok",
        "commence_time": "2099-06-19T18:00:00Z",
        "soft_observed_at": ref - 30.0,
        "sharp_observed_at": ref - 30.0,
        "soft_source": "nesine",
        "soft_odds": 2.1,
        "sharp_odds": 2.0,
        "market": "MS1",
    }
    base.update(overrides)
    return base


class NotificationQualityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        update_scan_league_settings(
            {
                "enabled_sport_keys": [
                    "soccer_fifa_world_cup",
                    "soccer_turkey_super_league",
                ],
            }
        )

    def test_theory_fixture_suite_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "notification_gate_theory.db"
            report = run_notification_gate_theory_suite(
                load_notification_gate_cases(_FIXTURE),
                db_path=db_path,
            )
            if report.failures:
                self.fail("\n".join(report.failures))
            self.assertTrue(report.ok)
            self.assertGreaterEqual(report.total, 7)

    def test_virtual_match_blocked(self) -> None:
        result = evaluate_notification_quality(
            _future_match(match_name="E-Futbol | A - B"),
            notify_key="a - b|MS1",
            mac_adi="E-Futbol | A - B",
            market="MS1",
            soft_odds=2.0,
            sharp_odds=1.9,
            tier="ACTION",
            ev=0.03,
            bypass_dedup=True,
            reference_at=_REFERENCE_AT,
        )
        self.assertFalse(result.allow)
        self.assertTrue(result.reason.startswith("theory_sanal"))

    def test_old_match_blocked(self) -> None:
        SCAN_CYCLE.reset()
        SCAN_CYCLE.register_qualifying("brazil - morocco|MS1")
        result = evaluate_notification_quality(
            _future_match(commence_time="2020-01-01T12:00:00Z"),
            notify_key="brazil - morocco|MS1",
            mac_adi="Brazil - Morocco",
            market="MS1",
            soft_odds=2.1,
            sharp_odds=2.0,
            tier="ACTION",
            ev=0.03,
            bypass_dedup=True,
            reference_at=_REFERENCE_AT,
        )
        self.assertFalse(result.allow)
        self.assertEqual(result.reason, "theory_mac_penceresi_kapandi")

    def test_missing_feed_timestamps_blocked(self) -> None:
        SCAN_CYCLE.reset()
        SCAN_CYCLE.register_qualifying("brazil - morocco|MS1")
        result = evaluate_notification_quality(
            _future_match(soft_observed_at=0.0, sharp_observed_at=0.0),
            notify_key="brazil - morocco|MS1",
            mac_adi="Brazil - Morocco",
            market="MS1",
            soft_odds=2.1,
            sharp_odds=2.0,
            tier="ACTION",
            ev=0.03,
            bypass_dedup=True,
            reference_at=_REFERENCE_AT,
        )
        self.assertFalse(result.allow)
        self.assertEqual(result.reason, "theory_feed_zaman_yok")

    def test_missing_event_id_blocked(self) -> None:
        SCAN_CYCLE.reset()
        SCAN_CYCLE.register_qualifying("brazil - morocco|MS1")
        result = evaluate_notification_quality(
            _future_match(event_id=""),
            notify_key="brazil - morocco|MS1",
            mac_adi="Brazil - Morocco",
            market="MS1",
            soft_odds=2.1,
            sharp_odds=2.0,
            tier="ACTION",
            ev=0.03,
            bypass_dedup=True,
            reference_at=_REFERENCE_AT,
        )
        self.assertFalse(result.allow)
        self.assertEqual(result.reason, "theory_event_id_yok")

    def test_outside_scan_cycle_blocked(self) -> None:
        SCAN_CYCLE.reset()
        result = evaluate_notification_quality(
            _future_match(),
            notify_key="brazil - morocco|MS1",
            mac_adi="Brazil - Morocco",
            market="MS1",
            soft_odds=2.1,
            sharp_odds=2.0,
            tier="ACTION",
            ev=0.03,
            bypass_dedup=True,
            reference_at=_REFERENCE_AT,
        )
        self.assertFalse(result.allow)
        self.assertEqual(result.reason, "theory_tarama_dongusu_disi")


if __name__ == "__main__":
    unittest.main()

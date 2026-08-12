import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import measurement_mode


class MeasurementModeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._state_patch = patch.object(
            measurement_mode, "MEASUREMENT_STATE_PATH", root / "measurement_mode.json"
        )
        self._db_patch = patch.object(measurement_mode, "SQE_DB_PATH", root / "olcum.db")
        self._state_patch.start()
        self._db_patch.start()

    def tearDown(self) -> None:
        self._state_patch.stop()
        self._db_patch.stop()
        self._tmp.cleanup()


class ModeSwitchTests(MeasurementModeTestCase):
    def test_default_is_disabled(self) -> None:
        self.assertFalse(measurement_mode.is_measurement_mode_enabled())

    def test_toggle_round_trip(self) -> None:
        self.assertTrue(measurement_mode.set_measurement_mode(True))
        self.assertTrue(measurement_mode.is_measurement_mode_enabled())
        self.assertTrue(measurement_mode.set_measurement_mode(False))
        self.assertFalse(measurement_mode.is_measurement_mode_enabled())

    def test_corrupt_state_is_treated_as_disabled(self) -> None:
        measurement_mode.MEASUREMENT_STATE_PATH.write_text("{bozuk", encoding="utf-8")
        self.assertFalse(measurement_mode.is_measurement_mode_enabled())


class SignalLedgerTests(MeasurementModeTestCase):
    def _record(self, **overrides: object) -> bool:
        payload: dict[str, object] = {
            "mac_adi": "Test A - Test B",
            "market": "ms1",
            "tier": "action",
            "soft_odds": 2.2,
            "sharp_odds": 2.0,
            "ev": 0.1,
            "league_name": "Test Ligi",
            "event_id": "evt-1",
            "commence_time": "2030-01-01T18:00:00Z",
            "consensus_source": "pinnacle",
            "consensus_books": 1,
            "fair_probability": 0.5,
            "stake": 120.0,
        }
        payload.update(overrides)
        return measurement_mode.record_signal(**payload)  # type: ignore[arg-type]

    def test_signal_is_persisted_with_normalized_fields(self) -> None:
        self.assertTrue(self._record())
        connection = sqlite3.connect(measurement_mode.SQE_DB_PATH)
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM olcum_sinyalleri").fetchone()
        connection.close()
        self.assertEqual(row["market"], "MS1")
        self.assertEqual(row["tier"], "ACTION")
        self.assertEqual(row["referans_kaynak"], "pinnacle")
        self.assertIsNone(row["clv_pct"])
        self.assertGreater(float(row["kickoffa_kalan_saat"]), 0.0)

    def test_funnel_rows_are_persisted(self) -> None:
        self.assertTrue(
            measurement_mode.record_scan_funnel(
                {"birlesik": 40, "zayif_referans": 12, "telegram": 2}, cycle_id="c1"
            )
        )
        connection = sqlite3.connect(measurement_mode.SQE_DB_PATH)
        rows = connection.execute(
            "SELECT asama, adet FROM olcum_huni ORDER BY asama"
        ).fetchall()
        connection.close()
        self.assertEqual(rows, [("birlesik", 40), ("telegram", 2), ("zayif_referans", 12)])

    def test_empty_funnel_is_ignored(self) -> None:
        self.assertFalse(measurement_mode.record_scan_funnel({}))

    def test_report_breaks_down_by_reference_and_flags_small_samples(self) -> None:
        self._record(consensus_source="pinnacle")
        self._record(consensus_source="exchange_consensus", market="alt2.5")
        card = measurement_mode.report(min_sample=2)
        self.assertEqual(card["toplam"]["sinyal"], 2)
        self.assertEqual(card["toplam"]["olculen"], 0)
        references = card["kirilimlar"]["referans"]
        self.assertEqual(set(references), {"pinnacle", "exchange_consensus"})
        self.assertFalse(references["pinnacle"]["yeterli_ornek"])
        self.assertEqual(set(card["kirilimlar"]["pazar"]), {"MS1", "ALT2.5"})


class ClvCaptureTests(MeasurementModeTestCase):
    def test_pending_signal_gets_closing_line_and_clv(self) -> None:
        measurement_mode.record_signal(
            mac_adi="Test A - Test B",
            market="MS1",
            tier="ACTION",
            soft_odds=2.2,
            sharp_odds=2.0,
            ev=0.1,
            event_id="evt-1",
            commence_time="2020-01-01T18:00:00Z",
        )
        closing = {"sharp_odds": 1.8, "obs_epoch": 1577901600.0, "books": 3}
        with patch("core.clv_tracker.get_index", return_value={}), patch(
            "core.clv_tracker.find_closing_sharp_odds", return_value=closing
        ):
            result = measurement_mode.capture_pending_clv()

        self.assertEqual(result["yakalandi"], 1)
        card = measurement_mode.report()
        self.assertEqual(card["toplam"]["olculen"], 1)
        # 2.00 -> 1.80 kapanis: oran lehe kisaldi, CLV pozitif.
        self.assertAlmostEqual(card["toplam"]["ort_clv_pct"], 11.111, places=2)
        self.assertEqual(card["toplam"]["pozitif_clv_orani"], 100.0)

    def test_future_kickoff_stays_pending(self) -> None:
        measurement_mode.record_signal(
            mac_adi="Test A - Test B",
            market="MS1",
            tier="ACTION",
            soft_odds=2.2,
            sharp_odds=2.0,
            ev=0.1,
            event_id="evt-2",
            commence_time="2099-01-01T18:00:00Z",
        )
        with patch("core.clv_tracker.get_index", return_value={}):
            result = measurement_mode.capture_pending_clv()
        self.assertEqual(result["beklemede"], 1)
        self.assertEqual(result["yakalandi"], 0)

    def test_missing_closing_observation_is_skipped(self) -> None:
        measurement_mode.record_signal(
            mac_adi="Test A - Test B",
            market="MS1",
            tier="ACTION",
            soft_odds=2.2,
            sharp_odds=2.0,
            ev=0.1,
            event_id="evt-3",
            commence_time="2020-01-01T18:00:00Z",
        )
        with patch("core.clv_tracker.get_index", return_value={}), patch(
            "core.clv_tracker.find_closing_sharp_odds", return_value=None
        ):
            result = measurement_mode.capture_pending_clv()
        self.assertEqual(result["atlandi"], 1)


class StatePayloadTests(MeasurementModeTestCase):
    def test_state_file_records_timestamp(self) -> None:
        measurement_mode.set_measurement_mode(True)
        payload = json.loads(measurement_mode.MEASUREMENT_STATE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(payload["enabled"])
        self.assertIn("updated_at", payload)


if __name__ == "__main__":
    unittest.main()

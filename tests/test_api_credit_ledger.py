from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from core import api_credit_ledger


class ApiCreditLedgerTests(unittest.TestCase):
    """Kredi defteri: canli database dosyasina DOKUNMAZ, gecici klasor kullanir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._path = Path(self._tmp.name) / "api_credit_ledger.json"
        patcher = mock.patch.object(api_credit_ledger, "_LEDGER_PATH", self._path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_record_accumulates_daily_spend(self) -> None:
        api_credit_ledger.record_odds_api_usage(
            last_cost="4", remaining="496", used="4", endpoint="oran"
        )
        api_credit_ledger.record_odds_api_usage(
            last_cost="2", remaining="494", used="6", endpoint="skor"
        )
        self.assertEqual(api_credit_ledger.get_today_spent(), 6.0)
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        day = next(iter(payload["days"]))
        self.assertEqual(payload["days"][day]["requests"], 2)
        self.assertEqual(payload["days"][day]["endpoints"]["oran"], 4.0)
        self.assertEqual(payload["days"][day]["endpoints"]["skor"], 2.0)
        self.assertEqual(payload["last_remaining"], 494.0)
        # atomik yazim: gecici .tmp dosyasi geride kalmamali
        self.assertFalse(self._path.with_name(self._path.name + ".tmp").exists())

    def test_bad_header_values_do_not_crash(self) -> None:
        api_credit_ledger.record_odds_api_usage(
            last_cost="abc", remaining=None, used="", endpoint=""
        )
        self.assertEqual(api_credit_ledger.get_today_spent(), 0.0)
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        day = next(iter(payload["days"]))
        self.assertEqual(payload["days"][day]["requests"], 1)

    def test_corrupt_ledger_falls_back_to_default(self) -> None:
        self._path.write_text("{bozuk json", encoding="utf-8")
        api_credit_ledger.record_odds_api_usage(
            last_cost="1", remaining="499", used="1", endpoint="saglik"
        )
        self.assertEqual(api_credit_ledger.get_today_spent(), 1.0)

    def test_panel_payload_allowance(self) -> None:
        api_credit_ledger.record_odds_api_usage(
            last_cost="4", remaining="450", used="50", endpoint="oran"
        )
        payload = api_credit_ledger.build_credit_panel_payload()
        self.assertEqual(payload["today_spent"], 4.0)
        self.assertEqual(payload["remaining"], 450.0)
        self.assertEqual(payload["plan_size"], 500.0)
        self.assertGreaterEqual(payload["days_left_in_month"], 1)
        self.assertIsNotNone(payload["daily_allowance"])
        self.assertGreaterEqual(payload["daily_allowance"], 450 // 31)
        self.assertIn("Bugun 4/", payload["status_label"])
        self.assertIn("Kalan 450", payload["status_label"])
        self.assertFalse(payload["cap_reached"])

    def test_panel_payload_without_data(self) -> None:
        payload = api_credit_ledger.build_credit_panel_payload()
        self.assertEqual(payload["today_spent"], 0.0)
        self.assertIsNone(payload["remaining"])
        self.assertEqual(
            payload["daily_allowance"], api_credit_ledger._DAILY_ALLOWANCE_FALLBACK
        )
        self.assertEqual(payload["status_label"], "Henuz API kullanimi olculmedi")

    def test_daily_allowance_modes(self) -> None:
        # Veri yokken taban tavan kullanilir
        self.assertEqual(
            api_credit_ledger.get_daily_allowance(),
            api_credit_ledger._DAILY_ALLOWANCE_FALLBACK,
        )
        # Kalan biliniyorsa: kalan // ayin kalan gunu
        api_credit_ledger.record_odds_api_usage(
            last_cost="4", remaining="500", used="0", endpoint="oran"
        )
        with mock.patch.object(api_credit_ledger, "_days_left_in_month", return_value=25):
            self.assertEqual(api_credit_ledger.get_daily_allowance(), 20)
        # Kalan tek sorguya bile yetmiyorsa tavan 0 (olu anahtar korumasi)
        api_credit_ledger.record_odds_api_usage(
            last_cost="0", remaining="0", used="500", endpoint="oran"
        )
        self.assertEqual(api_credit_ledger.get_daily_allowance(), 0)

    def test_key_change_resets_remaining_knowledge(self) -> None:
        api_credit_ledger.record_odds_api_usage(
            last_cost="4", remaining="0", used="20000", endpoint="oran"
        )
        self.assertEqual(api_credit_ledger.get_last_remaining(), 0.0)
        # Anahtar degisti: eski "kalan 0" yeni anahtari KILITLEMEMELI
        with mock.patch.object(api_credit_ledger, "ODDS_API_KEY", "yeni-anahtar-9999"):
            self.assertIsNone(api_credit_ledger.get_last_remaining())
            self.assertEqual(
                api_credit_ledger.get_daily_allowance(),
                api_credit_ledger._DAILY_ALLOWANCE_FALLBACK,
            )

    def test_cap_info_blocks_when_spent_reaches_allowance(self) -> None:
        api_credit_ledger.record_odds_api_usage(
            last_cost="8", remaining="4", used="496", endpoint="oran"
        )
        info = api_credit_ledger.get_today_cap_info()
        self.assertEqual(info["allowance"], 1)  # kalan 4 -> gunde en az 1 hak
        self.assertEqual(info["affordable_fetches"], 0)  # 8 harcandi, hak bitti
        self.assertTrue(info["cap_reached"])

    def test_month_sum_ignores_other_months(self) -> None:
        now_local = datetime.now(timezone.utc).astimezone(api_credit_ledger._DAY_TIMEZONE)
        today = now_local.date().isoformat()
        ledger = {
            "days": {
                "2001-01-15": {"spent": 99.0, "requests": 9, "endpoints": {}},
                today: {"spent": 3.0, "requests": 1, "endpoints": {}},
            },
            "last_remaining": 400.0,
            "last_used": 100.0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._path.write_text(json.dumps(ledger), encoding="utf-8")
        self.assertEqual(api_credit_ledger.get_month_spent(), 3.0)
        self.assertEqual(api_credit_ledger.get_month_spent("2001-01"), 99.0)


if __name__ == "__main__":
    unittest.main()

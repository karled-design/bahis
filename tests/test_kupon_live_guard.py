from __future__ import annotations

import unittest

from core.kupon_live_guard import is_live_kupon_metadata, is_live_kupon_row


class KuponLiveGuardTests(unittest.TestCase):
    def test_live_metadata_requires_event_and_commence(self) -> None:
        ok, reason = is_live_kupon_metadata(
            event_id="abc123",
            commence_time="2099-06-19T18:00:00Z",
            sport_key="soccer_turkey_super_league",
            mac_adi="Galatasaray - Fenerbahce",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_dry_run_event_rejected(self) -> None:
        ok, reason = is_live_kupon_metadata(
            event_id="dry-run-event-1",
            commence_time="2099-06-19T18:00:00Z",
            sport_key="soccer_turkey_super_league",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "dry_run_event")

    def test_missing_event_id_rejected(self) -> None:
        ok, reason = is_live_kupon_metadata(
            event_id="",
            commence_time="2099-06-19T18:00:00Z",
            sport_key="soccer_turkey_super_league",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "event_id_yok")

    def test_live_kupon_row_helper(self) -> None:
        self.assertTrue(
            is_live_kupon_row(
                {
                    "event_id": "live-001",
                    "commence_time": "2099-06-19T18:00:00Z",
                    "sport_key": "soccer_epl",
                    "mac_adi": "Arsenal - Chelsea",
                }
            )
        )
        self.assertFalse(
            is_live_kupon_row(
                {
                    "event_id": "",
                    "commence_time": "2099-06-19T18:00:00Z",
                    "sport_key": "soccer_epl",
                    "mac_adi": "Arsenal - Chelsea",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()

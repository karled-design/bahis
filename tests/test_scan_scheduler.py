from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import scan_scheduler


_T0 = datetime(2026, 7, 6, 9, 0, 0, tzinfo=timezone.utc)


class ScanSchedulerTests(unittest.TestCase):
    """Pencere planlayicisi: canli dosyalara DOKUNMAZ, sabit saatle calisir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        state_path = Path(self._tmp.name) / "scan_window_state.json"
        patcher = mock.patch.object(scan_scheduler, "_STATE_PATH", state_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.kickoffs: dict[str, list[datetime]] = {}
        self.known: set[str] = set()
        patcher = mock.patch.object(
            scan_scheduler,
            "get_league_kickoffs",
            side_effect=lambda key: list(self.kickoffs.get(key, [])),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            scan_scheduler,
            "has_kickoff_data",
            side_effect=lambda key: key in self.known,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _set_league(self, key: str, kickoffs: list[datetime]) -> None:
        self.kickoffs[key] = kickoffs
        self.known.add(key)

    def test_windows_open_at_t4h_and_t45m_only_once_each(self) -> None:
        key = "soccer_fifa_world_cup"
        kickoff = _T0 + timedelta(hours=5)
        self._set_league(key, [kickoff])

        # Pencereden once: sorgu hakki yok
        due, reason = scan_scheduler.should_fetch_league_now(key, now=_T0)
        self.assertFalse(due)
        self.assertIn("siradaki pencere", reason)

        # T-4 saat penceresi acildi
        at_open = _T0 + timedelta(hours=1, minutes=5)
        due, _ = scan_scheduler.should_fetch_league_now(key, now=at_open)
        self.assertTrue(due)

        # Sorgu yapildi -> ayni pencere ikinci kez yakilmaz
        scan_scheduler.mark_league_fetched(key, now=at_open)
        due, _ = scan_scheduler.should_fetch_league_now(
            key, now=at_open + timedelta(minutes=10)
        )
        self.assertFalse(due)

        # T-45 dk penceresi ayrica acilir
        at_close = _T0 + timedelta(hours=4, minutes=20)
        due, _ = scan_scheduler.should_fetch_league_now(key, now=at_close)
        self.assertTrue(due)
        scan_scheduler.mark_league_fetched(key, now=at_close)

        # Mac basladiktan 35 dk sonra pencere tamamen kapanir
        after_ko = kickoff + timedelta(minutes=35)
        due, _ = scan_scheduler.should_fetch_league_now(key, now=after_ko)
        self.assertFalse(due)

    def test_missed_window_stays_claimable_until_after_kickoff(self) -> None:
        key = "soccer_epl"
        kickoff = _T0 + timedelta(hours=1)
        self._set_league(key, [kickoff])
        # Iki pencere de kacirilmis ama mac + 20 dk henuz gecmemis -> hala hak var
        due, _ = scan_scheduler.should_fetch_league_now(
            key, now=kickoff + timedelta(minutes=20)
        )
        self.assertTrue(due)

    def test_close_slots_are_merged(self) -> None:
        key = "soccer_brazil_campeonato"
        self._set_league(
            key,
            [_T0 + timedelta(hours=1), _T0 + timedelta(hours=1, minutes=10)],
        )
        slots = scan_scheduler._league_slots(key, _T0)
        # 2 mac x 2 pencere = 4 ham pencere; yakin ciftler birlesip 2 kalir
        self.assertEqual(len(slots), 2)

    def test_fallback_pace_when_schedule_unknown(self) -> None:
        key = "soccer_turkey_super_league"  # bilinmeyen takvim (self.known'a eklenmedi)
        due, reason = scan_scheduler.should_fetch_league_now(key, now=_T0)
        self.assertTrue(due)
        self.assertIn("yedek tempo", reason)

        scan_scheduler.mark_league_fetched(key, now=_T0)
        due, _ = scan_scheduler.should_fetch_league_now(
            key, now=_T0 + timedelta(hours=2)
        )
        self.assertFalse(due)
        due, _ = scan_scheduler.should_fetch_league_now(
            key, now=_T0 + timedelta(hours=4, minutes=1)
        )
        self.assertTrue(due)

    def test_mark_survives_restart_via_disk_state(self) -> None:
        key = "soccer_fifa_world_cup"
        kickoff = _T0 + timedelta(hours=4, minutes=30)
        self._set_league(key, [kickoff])
        at_open = _T0 + timedelta(hours=1)
        scan_scheduler.mark_league_fetched(key, now=at_open)
        # "Yeniden baslatma": planlayicida bellek yok, durum diskten okunur
        due, _ = scan_scheduler.should_fetch_league_now(
            key, now=at_open + timedelta(minutes=5)
        )
        self.assertFalse(due)

    def test_select_due_leagues_returns_note_when_idle(self) -> None:
        active = "soccer_fifa_world_cup"
        idle = "soccer_epl"
        # Maclar 5 ve 30 saat sonra: su an hicbir pencere acik degil
        self._set_league(active, [_T0 + timedelta(hours=5)])
        self._set_league(idle, [_T0 + timedelta(hours=30)])

        due, note = scan_scheduler.select_due_leagues([active, idle], now=_T0)
        self.assertEqual(due, [])
        self.assertIn("siradaki pencere", note)

        # 1,5 saat sonra: yakin macin T-4s penceresi acildi, uzak mac hala bekliyor
        due, note = scan_scheduler.select_due_leagues(
            [active, idle], now=_T0 + timedelta(hours=1, minutes=30)
        )
        self.assertEqual(due, [active])
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()

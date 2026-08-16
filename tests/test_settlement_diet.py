from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import auto_settler
from scrapers import sharp_feed
from tests.live_state_isolation import isolate_module

isolate_module(globals())  # canli database dosyalari yerine gecici klasor


_T0 = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


class SettlementDietTests(unittest.TestCase):
    """Skor sorgusu diyeti (Adim 4): dolgu yok, mac bitmeden sorgu yok.

    Testler ag cagrisi yapmaz ve canli dosyalara DOKUNMAZ.
    """

    def test_split_ready_blocks_until_buffer_after_kickoff(self) -> None:
        mac_suruyor = {"id": 1, "commence_time": (_T0 - timedelta(hours=1)).isoformat()}
        mac_bitmis = {"id": 2, "commence_time": (_T0 - timedelta(hours=3)).isoformat()}
        takvim_yok = {"id": 3}

        ready, earliest = auto_settler._split_settlement_ready(
            [mac_suruyor, mac_bitmis, takvim_yok], now=_T0
        )

        # Bitmis mac + takvimi bilinmeyen kupon sorgulanabilir (fail-open);
        # henuz suren mac beklemede kalir.
        self.assertEqual([k["id"] for k in ready], [2, 3])
        self.assertEqual(earliest, _T0 + timedelta(hours=1))

    def test_run_pass_skips_feed_when_no_match_finished(self) -> None:
        now = datetime.now(timezone.utc)
        kupon = {
            "id": 7,
            "sport_key": "soccer_epl",
            "commence_time": (now + timedelta(hours=3)).isoformat(),
            "eklenme_tarihi": now.isoformat(),
        }
        with mock.patch.object(auto_settler, "backfill_pending_kupon_fixture_metadata"), \
                mock.patch.object(
                    auto_settler.clv_tracker,
                    "backfill_missing",
                    return_value={"yakalandi": 0},
                ), \
                mock.patch.object(auto_settler, "get_pending_kupons", return_value=[kupon]), \
                mock.patch.object(
                    auto_settler,
                    "get_settlement_feed",
                    side_effect=AssertionError("mac bitmeden skor sorgusu atildi"),
                ), \
                mock.patch.object(auto_settler, "append_settlement_cycle_log") as log_mock:
            stats = auto_settler.run_settlement_pass()

        self.assertEqual(stats["fetched"], 0)
        self.assertEqual(stats["waiting"], 1)
        log_mock.assert_called_once()
        log_record = log_mock.call_args.args[0]
        self.assertEqual(log_record["skipped_fetch"], "mac_bitmedi")

    def test_run_pass_fetches_only_ready_kupon_league(self) -> None:
        now = datetime.now(timezone.utc)
        bitmis = {
            "id": 1,
            "sport_key": "soccer_fifa_world_cup",
            "commence_time": (now - timedelta(hours=3)).isoformat(),
            "eklenme_tarihi": (now - timedelta(hours=4)).isoformat(),
        }
        suruyor = {
            "id": 2,
            "sport_key": "soccer_epl",
            "commence_time": (now - timedelta(minutes=30)).isoformat(),
            "eklenme_tarihi": (now - timedelta(hours=1)).isoformat(),
        }
        seen_keys: list[tuple[str, ...]] = []

        def fake_feed(*, extra_sport_keys):
            seen_keys.append(tuple(extra_sport_keys))
            return []

        with mock.patch.object(auto_settler, "backfill_pending_kupon_fixture_metadata"), \
                mock.patch.object(
                    auto_settler.clv_tracker,
                    "backfill_missing",
                    return_value={"yakalandi": 0},
                ), \
                mock.patch.object(
                    auto_settler, "get_pending_kupons", return_value=[bitmis, suruyor]
                ), \
                mock.patch.object(auto_settler, "get_settlement_feed", side_effect=fake_feed), \
                mock.patch.object(auto_settler, "append_settlement_cycle_log"):
            stats = auto_settler.run_settlement_pass()

        # Yalniz maci bitmis kuponun ligi sorgulanir; suren macin ligi (EPL)
        # bu turda sorgulanmaz.
        self.assertEqual(seen_keys, [("soccer_fifa_world_cup",)])
        self.assertEqual(stats["fetched"], 1)

    def test_run_pass_captures_measurement_clv_without_kupons(self) -> None:
        # Olcum modunda kupon acilmaz; CLV yakalama yine de her turda calismali.
        with mock.patch.object(auto_settler, "backfill_pending_kupon_fixture_metadata"), \
                mock.patch.object(
                    auto_settler.clv_tracker,
                    "backfill_missing",
                    return_value={"yakalandi": 0},
                ), \
                mock.patch.object(
                    auto_settler.measurement_mode,
                    "capture_pending_clv",
                    return_value={"toplam": 2, "yakalandi": 2, "atlandi": 0, "beklemede": 0},
                ) as olcum_mock, \
                mock.patch.object(auto_settler, "get_pending_kupons", return_value=[]), \
                mock.patch.object(
                    auto_settler,
                    "get_settlement_feed",
                    side_effect=AssertionError("kupon yokken skor sorgusu atildi"),
                ), \
                mock.patch.object(auto_settler, "append_settlement_cycle_log"):
            auto_settler.run_settlement_pass()

        olcum_mock.assert_called_once()

    def test_fetch_settlement_results_queries_only_kupon_leagues(self) -> None:
        calls: list[str] = []

        def fake_fetch(sport_key: str):
            calls.append(sport_key)
            return []

        with mock.patch.object(
            sharp_feed, "_fetch_sport_settlement_results", side_effect=fake_fetch
        ), mock.patch.object(
            sharp_feed,
            "_select_rotated_settlement_sport_keys",
            side_effect=AssertionError("kupon ligi belliyken rotasyon dolgusu cagrildi"),
        ):
            sharp_feed.fetch_settlement_results(
                extra_sport_keys=("soccer_epl", "soccer_epl", "soccer_italy_serie_a")
            )

        self.assertEqual(calls, ["soccer_epl", "soccer_italy_serie_a"])

    def test_fetch_settlement_results_rotation_fallback_when_league_unknown(self) -> None:
        calls: list[str] = []

        def fake_fetch(sport_key: str):
            calls.append(sport_key)
            return []

        with mock.patch.object(
            sharp_feed, "_fetch_sport_settlement_results", side_effect=fake_fetch
        ), mock.patch.object(
            sharp_feed,
            "_select_rotated_settlement_sport_keys",
            return_value=["soccer_fifa_world_cup"],
        ):
            sharp_feed.fetch_settlement_results(extra_sport_keys=())

        self.assertEqual(calls, ["soccer_fifa_world_cup"])

    def test_next_sleep_uses_diet_pace_only_after_billed_fetch(self) -> None:
        # Faturali sorgu atildi -> 45 dk; atilmadi -> 15 dk ucretsiz tempo.
        self.assertEqual(
            auto_settler._next_sleep_seconds({"pending": 2, "fetched": 1}),
            auto_settler._POST_FETCH_SLEEP_SECONDS,
        )
        self.assertEqual(
            auto_settler._next_sleep_seconds({"pending": 2, "fetched": 0}),
            auto_settler._IDLE_CYCLE_SLEEP_SECONDS,
        )
        self.assertEqual(
            auto_settler._next_sleep_seconds({"pending": 0, "fetched": 0}),
            auto_settler._IDLE_CYCLE_SLEEP_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()

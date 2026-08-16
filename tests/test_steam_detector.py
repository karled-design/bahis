"""Steam (gecikmeli fiyat) avcisi testleri."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from core import steam_detector
from core.steam_detector import build_history, detect_steam


_NOW = 1_800_000_000.0


def _kickoff_iso(hours_ahead: float) -> str:
    moment = datetime.fromtimestamp(_NOW, tz=timezone.utc) + timedelta(hours=hours_ahead)
    return moment.isoformat().replace("+00:00", "Z")


def _match(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "match_name": "Feyenoord - Ajax",
        "market": "MS1",
        "league_name": "Eredivisie",
        "sport_key": "soccer_netherlands_eredivisie",
        "event_id": "evt-1",
        "commence_time": _kickoff_iso(3.0),
        "consensus_source": "pinnacle",
        "consensus_books": 1,
        "sharp_odds": 1.90,
        "soft_odds": 2.05,
        "observed_at": _NOW,
        "sharp_observed_at": _NOW,
        "soft_observed_at": _NOW,
    }
    record.update(overrides)
    return record


def _history(
    sharp: float,
    soft: float,
    *,
    minutes_ago: float = 60.0,
    key: tuple[str, str] = ("evt-1", "MS1"),
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    return {
        key: [
            {
                "sharp_odds": sharp,
                "soft_odds": soft,
                "observed_at": _NOW - minutes_ago * 60.0,
            }
        ]
    }


class SteamDetectionTests(unittest.TestCase):
    def test_sharp_drop_with_stale_soft_price_creates_signal(self) -> None:
        signals = detect_steam(
            [_match()],
            history=_history(2.05, 2.05),
            now=_NOW,
        )

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertAlmostEqual(signal.sharp_drop, (2.05 - 1.90) / 2.05, places=5)
        self.assertEqual(signal.onceki_sharp, 2.05)
        self.assertEqual(signal.sharp_odds, 1.90)
        self.assertEqual(signal.key, ("evt-1", "MS1"))

    def test_soft_price_following_the_move_is_not_steam(self) -> None:
        # Nesine de fiyati indirdiyse gecikme yok: sinyal uretilmez.
        signals = detect_steam(
            [_match(soft_odds=1.92)],
            history=_history(2.05, 2.20),
            now=_NOW,
        )
        self.assertEqual(signals, [])

    def test_small_sharp_move_is_ignored(self) -> None:
        signals = detect_steam(
            [_match(sharp_odds=2.03)],
            history=_history(2.05, 2.05),
            now=_NOW,
        )
        self.assertEqual(signals, [])

    def test_soft_reference_quality_is_rejected(self) -> None:
        # Yumusak piyasa ortalamasina karsi olculen hareket edge degildir.
        signals = detect_steam(
            [_match(consensus_source="market_consensus")],
            history=_history(2.05, 2.05),
            now=_NOW,
        )
        self.assertEqual(signals, [])

    def test_imminent_kickoff_is_skipped(self) -> None:
        signals = detect_steam(
            [_match(commence_time=_kickoff_iso(0.1))],
            history=_history(2.05, 2.05),
            now=_NOW,
        )
        self.assertEqual(signals, [])

    def test_observation_outside_lookback_window_is_ignored(self) -> None:
        signals = detect_steam(
            [_match()],
            history=_history(2.05, 2.05, minutes_ago=48 * 60.0),
            now=_NOW,
        )
        self.assertEqual(signals, [])

    def test_missing_history_produces_no_signal(self) -> None:
        self.assertEqual(detect_steam([_match()], history={}, now=_NOW), [])


class SteamHistoryTests(unittest.TestCase):
    def test_build_history_reads_snapshots_within_window(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "20260101_000000_live.json").write_text(
                json.dumps(
                    {
                        "saved_at": _NOW - 3600.0,
                        "matches": [_match(sharp_odds=2.05, observed_at=_NOW - 3600.0,
                                           sharp_observed_at=_NOW - 3600.0)],
                    }
                ),
                encoding="utf-8",
            )
            (directory / "20251201_000000_live.json").write_text(
                json.dumps(
                    {
                        "saved_at": _NOW - 90 * 3600.0,
                        "matches": [_match(sharp_odds=3.00, observed_at=_NOW - 90 * 3600.0,
                                           sharp_observed_at=_NOW - 90 * 3600.0)],
                    }
                ),
                encoding="utf-8",
            )

            history = build_history(now=_NOW, snapshot_dir=directory)

        self.assertEqual(list(history), [("evt-1", "MS1")])
        self.assertEqual([item["sharp_odds"] for item in history[("evt-1", "MS1")]], [2.05])


class SteamCooldownTests(unittest.TestCase):
    def test_notified_signal_is_filtered_until_cooldown_expires(self) -> None:
        signal = detect_steam([_match()], history=_history(2.05, 2.05), now=_NOW)[0]

        with TemporaryDirectory() as tmp:
            with mock.patch.object(
                steam_detector, "_STATE_PATH", Path(tmp) / "steam_state.json"
            ):
                self.assertEqual(steam_detector.filter_uncooled([signal], now=_NOW), [signal])
                self.assertTrue(steam_detector.mark_notified(signal, now=_NOW))
                self.assertEqual(steam_detector.filter_uncooled([signal], now=_NOW), [])
                later = _NOW + steam_detector.COOLDOWN_SECONDS + 1.0
                self.assertEqual(
                    steam_detector.filter_uncooled([signal], now=later), [signal]
                )


if __name__ == "__main__":
    unittest.main()

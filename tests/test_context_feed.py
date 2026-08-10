from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.context_cache import init_context_cache
from scrapers.context_feed import (
    build_context_bundle_for_match,
    enrich_matches_with_context,
    get_last_context_feed_diag,
    is_api_football_configured,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "context"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _real_match() -> dict:
    return {
        "event_id": "ctx-feed-event-001",
        "sport_key": "soccer_turkey_super_league",
        "match_name": "Galatasaray - Fenerbahce",
        "league_name": "Turkiye Super Lig",
        "commence_time": "2026-06-19T18:00:00Z",
        "match_quality": "ok",
        "sharp_odds": 2.1,
        "soft_odds": 2.2,
    }


class ContextFeedMockApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._fixture_payloads = {
            "/fixtures": _load_fixture("api_fixtures_tr_super_lig.json"),
            "/standings": _load_fixture("api_standings_tr_super_lig.json"),
            "/injuries": _load_fixture("api_injuries_gs_fb.json"),
            "/fixtures/headtohead": _load_fixture("api_h2h_gs_fb.json"),
        }
        self._api_calls: list[tuple[str, dict]] = []

        def _mock_fetch(path: str, params: dict) -> dict | None:
            self._api_calls.append((path, dict(params)))
            return self._fixture_payloads.get(path)

        self.mock_fetch = _mock_fetch

    def _patch_db(self, db_path: Path):
        import database.db_manager as db_manager

        original = db_manager._DB_PATH
        db_manager._DB_PATH = db_path
        return original

    def _restore_db(self, original: Path) -> None:
        import database.db_manager as db_manager

        db_manager._DB_PATH = original

    def test_skips_virtual_match(self) -> None:
        virtual = {
            **_real_match(),
            "match_name": "E-Futbol | Galatasaray - Fenerbahce",
        }
        bundle = build_context_bundle_for_match(virtual, fetch_api=self.mock_fetch)
        self.assertIsNone(bundle)
        self.assertEqual(len(self._api_calls), 0)

    def test_skips_unknown_league(self) -> None:
        unknown = {
            **_real_match(),
            "sport_key": "soccer_fifa_world_cup",
        }
        bundle = build_context_bundle_for_match(unknown, fetch_api=self.mock_fetch)
        self.assertIsNone(bundle)
        diag = get_last_context_feed_diag()
        self.assertGreaterEqual(int(diag.get("skipped_no_league", 0)), 1)

    def test_builds_bundle_with_mock_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "context_feed.db"
            original = self._patch_db(db_path)
            try:
                connection = sqlite3.connect(db_path)
                init_context_cache(connection)
                connection.commit()
                connection.close()

                bundle = build_context_bundle_for_match(_real_match(), fetch_api=self.mock_fetch)
                self.assertIsNotNone(bundle)
                assert bundle is not None
                self.assertIn("standings", bundle)
                self.assertIn("injuries", bundle)
                self.assertIn("h2h", bundle)
                self.assertEqual(bundle["standings"]["sport_key"], "soccer_turkey_super_league")
                self.assertEqual(len(bundle["injuries"]["home_absences"]), 1)
                self.assertEqual(len(bundle["h2h"]["matches"]), 2)
                self.assertGreaterEqual(len(self._api_calls), 3)
            finally:
                self._restore_db(original)

    def test_builds_bundle_with_abbreviated_team_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "context_feed_abbr.db"
            original = self._patch_db(db_path)
            try:
                connection = sqlite3.connect(db_path)
                init_context_cache(connection)
                connection.commit()
                connection.close()

                abbreviated = {
                    **_real_match(),
                    "event_id": "ctx-feed-event-abbr",
                    "match_name": "GS - FB",
                }
                bundle = build_context_bundle_for_match(abbreviated, fetch_api=self.mock_fetch)
                self.assertIsNotNone(bundle)
            finally:
                self._restore_db(original)

    def test_second_call_uses_bundle_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "context_feed_cache.db"
            original = self._patch_db(db_path)
            try:
                connection = sqlite3.connect(db_path)
                init_context_cache(connection)
                connection.commit()
                connection.close()

                match = _real_match()
                first = build_context_bundle_for_match(match, fetch_api=self.mock_fetch)
                calls_after_first = len(self._api_calls)
                second = build_context_bundle_for_match(match, fetch_api=self.mock_fetch)
                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                self.assertEqual(first, second)
                self.assertEqual(len(self._api_calls), calls_after_first)
            finally:
                self._restore_db(original)

    def test_enrich_matches_attaches_context_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "context_feed_enrich.db"
            original = self._patch_db(db_path)
            try:
                connection = sqlite3.connect(db_path)
                init_context_cache(connection)
                connection.commit()
                connection.close()

                enriched = enrich_matches_with_context([_real_match()], fetch_api=self.mock_fetch)
                self.assertEqual(len(enriched), 1)
                self.assertIn("context_bundle", enriched[0])
                diag = get_last_context_feed_diag()
                self.assertEqual(int(diag.get("bundle_attached", 0)), 1)
            finally:
                self._restore_db(original)


class ContextFeedConfigTests(unittest.TestCase):
    def test_is_not_configured_without_key(self) -> None:
        with patch("scrapers.context_feed.API_FOOTBALL_KEY", ""):
            self.assertFalse(is_api_football_configured())

    def test_no_live_api_without_key(self) -> None:
        with patch("scrapers.context_feed.API_FOOTBALL_KEY", ""):
            bundle = build_context_bundle_for_match(_real_match(), fetch_api=None)
            self.assertIsNone(bundle)


if __name__ == "__main__":
    unittest.main()

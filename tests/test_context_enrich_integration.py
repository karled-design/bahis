from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.context_cache import init_context_cache
from scrapers.context_feed import (
    enrich_match_feed_safely,
    enrich_matches_with_context,
    should_run_context_enrich,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "context"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _real_match_row(*, market: str, event_id: str = "ctx-enrich-event-001") -> dict:
    return {
        "event_id": event_id,
        "sport_key": "soccer_turkey_super_league",
        "match_name": "Galatasaray - Fenerbahce",
        "market": market,
        "league_name": "Turkiye Super Lig",
        "commence_time": "2026-06-19T18:00:00Z",
        "match_quality": "ok",
        "sharp_odds": 2.1,
        "soft_odds": 2.2,
    }


class ContextEnrichModeTests(unittest.TestCase):
    def test_auto_mode_requires_api_key(self) -> None:
        with patch("scrapers.context_feed.API_FOOTBALL_KEY", ""):
            with patch("scrapers.context_feed.CONTEXT_ENRICH_MODE", "auto"):
                self.assertFalse(should_run_context_enrich())
        with patch("scrapers.context_feed.API_FOOTBALL_KEY", "test-key"):
            with patch("scrapers.context_feed.CONTEXT_ENRICH_MODE", "auto"):
                self.assertTrue(should_run_context_enrich())

    def test_off_mode_never_enriches(self) -> None:
        rows = [_real_match_row(market="MS1")]
        with patch("scrapers.context_feed.API_FOOTBALL_KEY", "test-key"):
            with patch("scrapers.context_feed.CONTEXT_ENRICH_MODE", "off"):
                result = enrich_match_feed_safely(rows)
        self.assertEqual(result, rows)
        self.assertNotIn("context_bundle", result[0])


class ContextEnrichDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._fixture_payloads = {
            "/fixtures": _load_fixture("api_fixtures_tr_super_lig.json"),
            "/standings": _load_fixture("api_standings_tr_super_lig.json"),
            "/injuries": _load_fixture("api_injuries_gs_fb.json"),
            "/fixtures/headtohead": _load_fixture("api_h2h_gs_fb.json"),
        }

        def _mock_fetch(path: str, params: dict) -> dict | None:
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

    def test_dedupes_same_event_across_markets(self) -> None:
        rows = [
            _real_match_row(market="MS1"),
            _real_match_row(market="X"),
            _real_match_row(market="MS2"),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "context_enrich_dedupe.db"
            original = self._patch_db(db_path)
            try:
                connection = sqlite3.connect(db_path)
                init_context_cache(connection)
                connection.commit()
                connection.close()

                with patch(
                    "scrapers.context_feed.build_context_bundle_for_match",
                    wraps=__import__("scrapers.context_feed", fromlist=["build_context_bundle_for_match"]).build_context_bundle_for_match,
                ) as build_mock:
                    enriched = enrich_matches_with_context(rows, fetch_api=self.mock_fetch)
                    self.assertEqual(build_mock.call_count, 1)
                self.assertEqual(len(enriched), 3)
                for row in enriched:
                    self.assertIn("context_bundle", row)
            finally:
                self._restore_db(original)

    def test_safe_enrich_returns_original_on_failure(self) -> None:
        rows = [_real_match_row(market="MS1")]
        with patch("scrapers.context_feed.should_run_context_enrich", return_value=True):
            with patch(
                "scrapers.context_feed.enrich_matches_with_context",
                side_effect=RuntimeError("boom"),
            ):
                result = enrich_match_feed_safely(rows)
        self.assertEqual(result, rows)


if __name__ == "__main__":
    unittest.main()

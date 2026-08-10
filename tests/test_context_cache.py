from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.match_filters import is_virtual_match_text
from database.context_cache import (
    REAL_MATCH_CACHE_FLAG,
    init_context_cache,
    is_cacheable_real_match,
    upsert_fixture_cache_row,
)
from database.db_manager import init_db, get_context_cache_stats


class ContextCacheSchemaTests(unittest.TestCase):
    def test_init_db_creates_context_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_sqe.db"
            original_path = self._patch_db_path(db_path)
            try:
                self.assertTrue(init_db())
                connection = sqlite3.connect(db_path)
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                connection.close()
                for table in (
                    "context_fixtures",
                    "context_standings",
                    "context_team_form",
                    "context_injuries",
                    "context_h2h",
                ):
                    self.assertIn(table, tables)
            finally:
                self._restore_db_path(original_path)

    def test_empty_context_cache_stats_are_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_sqe.db"
            original_path = self._patch_db_path(db_path)
            try:
                self.assertTrue(init_db())
                stats = get_context_cache_stats()
                self.assertEqual(stats.get("context_fixtures", -1), 0)
                self.assertEqual(stats.get("context_injuries", -1), 0)
            finally:
                self._restore_db_path(original_path)

    def _patch_db_path(self, db_path: Path):
        import database.db_manager as db_manager

        original = db_manager._DB_PATH
        db_manager._DB_PATH = db_path
        return original

    def _restore_db_path(self, original: Path) -> None:
        import database.db_manager as db_manager

        db_manager._DB_PATH = original


class RealMatchOnlyCacheTests(unittest.TestCase):
    def _real_match(self) -> dict:
        return {
            "event_id": "real-event-001",
            "sport_key": "soccer_turkey_super_league",
            "match_name": "Galatasaray - Fenerbahce",
            "league_name": "Turkiye Super Lig",
            "commence_time": "2026-06-19T18:00:00Z",
            "match_quality": "ok",
            "sharp_odds": 2.1,
            "soft_odds": 2.2,
        }

    def test_rejects_virtual_match_names(self) -> None:
        virtual = {
            **self._real_match(),
            "match_name": "E-Futbol | Team A - Team B",
        }
        self.assertTrue(is_virtual_match_text(virtual["match_name"]))
        self.assertFalse(is_cacheable_real_match(virtual))

    def test_rejects_suspicious_match_quality(self) -> None:
        suspicious = {
            **self._real_match(),
            "match_quality": "suspicious",
            "soft_odds": 11.9,
            "sharp_odds": 1.32,
        }
        self.assertFalse(is_cacheable_real_match(suspicious))

    def test_rejects_dry_run_event_id(self) -> None:
        dry_run = {
            **self._real_match(),
            "event_id": "dry-run-event-0",
        }
        self.assertFalse(is_cacheable_real_match(dry_run))

    def test_accepts_real_match(self) -> None:
        self.assertTrue(is_cacheable_real_match(self._real_match()))

    def test_upsert_skips_virtual_and_writes_real(self) -> None:
        connection = sqlite3.connect(":memory:")
        init_context_cache(connection)

        virtual = {
            **self._real_match(),
            "match_name": "Esoccer Battle | X - Y",
        }
        self.assertFalse(
            upsert_fixture_cache_row(
                connection,
                match=virtual,
                fetched_at="2026-06-19T10:00:00+00:00",
                expires_at="2026-06-19T12:00:00+00:00",
            )
        )

        self.assertTrue(
            upsert_fixture_cache_row(
                connection,
                match=self._real_match(),
                fetched_at="2026-06-19T10:00:00+00:00",
                expires_at="2026-06-19T12:00:00+00:00",
            )
        )

        row = connection.execute(
            "SELECT is_real_match, match_name FROM context_fixtures"
        ).fetchone()
        connection.close()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(int(row[0]), REAL_MATCH_CACHE_FLAG)
        self.assertEqual(row[1], "Galatasaray - Fenerbahce")


if __name__ == "__main__":
    unittest.main()

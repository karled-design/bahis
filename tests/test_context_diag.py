from __future__ import annotations

import json
import unittest
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from core.context_diag import (
    build_context_diag_report,
    filter_matches_for_date,
    format_context_diag_report,
    group_matches_by_event,
)
from tools.context_diag import _load_fixture_matches, main

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "context" / "tur_super_lig_gs_fb.json"
_FIXTURE_DATE = date(2026, 2, 23)


def _fixture_rows() -> list[dict]:
    return _load_fixture_matches(_FIXTURE_PATH)


class ContextDiagCoreTests(unittest.TestCase):
    def test_filters_virtual_and_date(self) -> None:
        rows = _fixture_rows()
        virtual = {
            **rows[0],
            "event_id": "virtual-1",
            "match_name": "E-Futbol | Galatasaray - Fenerbahce",
        }
        wrong_day = {**rows[0], "event_id": "wrong-day", "commence_time": "2026-03-01T17:00:00Z"}
        payload = rows + [virtual, wrong_day]

        filtered, virtual_skip, no_date, wrong_date = filter_matches_for_date(
            payload,
            reference_date=_FIXTURE_DATE,
        )
        self.assertEqual(len(filtered), 3)
        self.assertEqual(virtual_skip, 1)
        self.assertEqual(wrong_date, 1)

    def test_groups_markets_under_single_event(self) -> None:
        groups = group_matches_by_event(_fixture_rows())
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(next(iter(groups.values()))), 3)

    def test_build_report_scores_gs_fb_fixture(self) -> None:
        report = build_context_diag_report(_fixture_rows(), reference_date=_FIXTURE_DATE)
        self.assertEqual(report.event_groups, 1)
        self.assertEqual(report.with_context, 1)
        row = report.rows[0]
        self.assertEqual(row.match_name, "Galatasaray - Fenerbahce")
        self.assertIsNotNone(row.home_bias_score)
        self.assertGreater(row.home_bias_score or 0.0, 0.0)
        ms1 = next(item for item in row.markets if item.market == "MS1")
        self.assertIsNotNone(ms1.final_market_score)
        self.assertGreater(ms1.final_market_score or 0.0, 0.0)

    def test_format_report_mentions_no_telegram(self) -> None:
        report = build_context_diag_report(_fixture_rows(), reference_date=_FIXTURE_DATE)
        text = format_context_diag_report(report)
        self.assertIn("Context Diag", text)
        self.assertIn("Galatasaray - Fenerbahce", text)
        self.assertIn("Telegram yok", text)


class ContextDiagCliTests(unittest.TestCase):
    def test_cli_fixture_mode_exits_zero(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = main(["--source", "fixture", "--fixture", str(_FIXTURE_PATH)])
        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Galatasaray - Fenerbahce", output)

    def test_cli_json_mode(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = main(["--source", "fixture", "--fixture", str(_FIXTURE_PATH), "--json"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["event_groups"], 1)
        self.assertEqual(len(payload["rows"]), 1)


if __name__ == "__main__":
    unittest.main()

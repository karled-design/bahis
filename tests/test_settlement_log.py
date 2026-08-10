from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.settlement_log import append_settlement_cycle_log, get_settlement_log_path


class SettlementLogTests(unittest.TestCase):
    def test_append_writes_jsonl_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "settlement_log.jsonl"
            append_settlement_cycle_log(
                {"pending_before": 2, "settled": 1, "waiting": 1},
                log_path=log_path,
            )
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["pending_before"], 2)
            self.assertEqual(payload["settled"], 1)
            self.assertIn("ts", payload)

    def test_default_log_path_under_database(self) -> None:
        path = get_settlement_log_path()
        self.assertEqual(path.name, "settlement_log.jsonl")
        self.assertEqual(path.parent.name, "database")


if __name__ == "__main__":
    unittest.main()

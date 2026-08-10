from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.theory_backtest import (
    FUSION_FP_REDUCTION_TARGET,
    load_backtest_cases,
    run_theory_backtest,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "theory" / "backtest_cases.json"


class TheoryBacktestTests(unittest.TestCase):
    def test_load_cases_rejects_invalid_and_sorts_by_time(self) -> None:
        cases = load_backtest_cases(_FIXTURE)
        self.assertGreaterEqual(len(cases), 6)
        times = [case.commence_time for case in cases]
        self.assertEqual(times, sorted(times))

    def test_virtual_and_suspicious_never_bet(self) -> None:
        report = run_theory_backtest(load_backtest_cases(_FIXTURE))
        walk = {row["case_id"]: row for row in report.walk_forward}
        self.assertFalse(walk["virtual-skip"]["bet_ev_only"])
        self.assertFalse(walk["virtual-skip"]["bet_ev_context"])
        self.assertFalse(walk["suspicious-skip"]["bet_ev_only"])
        self.assertFalse(walk["suspicious-skip"]["bet_ev_context"])

    def test_ev_context_takes_fewer_or_equal_bets_than_ev_only(self) -> None:
        report = run_theory_backtest(load_backtest_cases(_FIXTURE))
        self.assertLessEqual(report.ev_context.bets, report.ev_only.bets)

    def test_ev_context_filters_negative_context_losses(self) -> None:
        report = run_theory_backtest(load_backtest_cases(_FIXTURE))
        walk = {row["case_id"]: row for row in report.walk_forward}
        self.assertTrue(walk["arsenal-chelsea-ms1-lost"]["bet_ev_only"])
        self.assertFalse(walk["arsenal-chelsea-ms1-lost"]["bet_ev_context"])
        self.assertTrue(walk["besiktas-trabzon-ms1-lost"]["bet_ev_only"])
        self.assertFalse(walk["besiktas-trabzon-ms1-lost"]["bet_ev_context"])

    def test_report_contains_fusion_gate_fields(self) -> None:
        report = run_theory_backtest(load_backtest_cases(_FIXTURE))
        payload = report.as_dict()
        self.assertIn("fusion_gate_pass", payload)
        self.assertIn("fusion_gate_reason", payload)
        self.assertIsInstance(payload["fusion_gate_reason"], str)

    def test_fixture_fp_reduction_meets_lab_target(self) -> None:
        report = run_theory_backtest(load_backtest_cases(_FIXTURE))
        if report.ev_only.false_positives <= 0:
            self.skipTest("fixture uretmedi EV-only false positive")
        self.assertGreaterEqual(report.fusion_fp_reduction, FUSION_FP_REDUCTION_TARGET)


if __name__ == "__main__":
    unittest.main()

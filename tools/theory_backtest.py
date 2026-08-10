#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.theory_backtest import load_backtest_cases, run_theory_backtest

_DEFAULT_CASES = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "theory" / "backtest_cases.json"
)


def _format_report_text(report: object) -> str:
    data = report.as_dict()
    ev_only = data["ev_only"]
    ev_context = data["ev_context"]
    lines = [
        "=== SQE-V1 Theory Backtest ===",
        f"cases={data['case_count']}",
        "",
        "[EV-ONLY]",
        f"  bets={ev_only['bets']} wins={ev_only['wins']} losses={ev_only['losses']} skipped={ev_only['skipped']}",
        f"  false_positives={ev_only['false_positives']} fp_rate={ev_only['false_positive_rate']:.4f}",
        f"  staked={ev_only['total_staked']:.2f} profit={ev_only['total_profit']:.2f} roi={ev_only['roi']:.4f}",
        f"  brier={ev_only['brier_score']}",
        "",
        "[EV+CONTEXT]",
        f"  bets={ev_context['bets']} wins={ev_context['wins']} losses={ev_context['losses']} skipped={ev_context['skipped']}",
        f"  false_positives={ev_context['false_positives']} fp_rate={ev_context['false_positive_rate']:.4f}",
        f"  staked={ev_context['total_staked']:.2f} profit={ev_context['total_profit']:.2f} roi={ev_context['roi']:.4f}",
        f"  brier={ev_context['brier_score']}",
        "",
        f"fusion_fp_reduction={data['fusion_fp_reduction']:.4f}",
        f"fusion_roi_delta={data['fusion_roi_delta']:.4f}",
        f"fusion_gate_pass={data['fusion_gate_pass']}",
        f"fusion_gate_reason={data['fusion_gate_reason']}",
        "",
        "NOT: Bu rapor theory-lab fixture verisindendir; canli gecmis oran kaniti degildir.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQE-V1 theory backtest (EV-only vs EV+context)")
    parser.add_argument(
        "--cases",
        type=Path,
        default=_DEFAULT_CASES,
        help="Backtest cases JSON yolu",
    )
    parser.add_argument("--json", action="store_true", help="JSON cikti")
    args = parser.parse_args(argv)

    if not args.cases.is_file():
        print(f"cases dosyasi bulunamadi: {args.cases}", file=sys.stderr)
        return 1

    cases = load_backtest_cases(args.cases)
    if not cases:
        print("Gecerli backtest case bulunamadi", file=sys.stderr)
        return 1

    report = run_theory_backtest(cases)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_report_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

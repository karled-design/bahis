#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time

from core.scan_pipeline import (
    ScanPipelineStats,
    count_legacy_action_candidates,
    count_current_work_orders,
    evaluate_matches,
)
from core.match_filters import reset_scan_cycle


def _run_live_diag() -> int:
    from scrapers.live_feed_gateway import get_unified_live_data

    started = time.perf_counter()
    reset_scan_cycle()
    matches = get_unified_live_data()
    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)

    candidates, stats = evaluate_matches(matches, current_kasa=1000.0, skip_freshness=True)
    legacy = count_legacy_action_candidates(matches)
    current = count_current_work_orders(candidates)

    try:
        from tools.snapshot_store import save_feed_snapshot

        snapshot_path = save_feed_snapshot(matches, label="diag")
        snapshot_note = f"snapshot={snapshot_path.name}"
    except OSError as exc:
        snapshot_note = f"snapshot=KAYDEDILEMEDI ({exc})"

    print("=== SQE-V1 Feed Diag ===")
    print(f"birlesik={stats.birlesik} | latency_ms={latency_ms} | {snapshot_note}")
    print(f"legacy_action(EV>=%3)={legacy}")
    print(
        f"current_total={current['total']} | watch={current['watch']} | "
        f"action={current['action']} | high={current['high']} | "
        f"actionable={current['actionable']}"
    )
    print(
        f"funnel: pasif_ev={stats.pasif_ev} | tolerans={stats.tolerans} | "
        f"stale={stats.stale} | efutbol={stats.efutbol} | "
        f"suspicious_match={stats.suspicious_match} | context_filter={stats.context_filter}"
    )

    delta_total = current["total"] - legacy
    delta_actionable = current["actionable"] - legacy
    if delta_total > 0:
        print(f"delta_total=+{delta_total} (eski kurula gore)")
    elif delta_total < 0:
        print(f"delta_total={delta_total} (eski kurula gore)")
    else:
        print("delta_total=0 (eski kurula gore)")

    if delta_actionable > 0:
        print(f"delta_actionable=+{delta_actionable}")
    else:
        print(f"delta_actionable={delta_actionable}")

    if candidates:
        print("--- ornek adaylar ---")
        for item in candidates[:5]:
            print(
                f"  {item.tier} | {item.match_name} | {item.market} | "
                f"EV=%{item.ev_percent} | soft={item.soft_odds} sharp={item.sharp_odds}"
            )
    return 0


def _run_dry_diag() -> int:
    from scrapers.live_feed_gateway import get_dry_run_live_data

    reset_scan_cycle()
    matches = get_dry_run_live_data()
    candidates, stats = evaluate_matches(matches, current_kasa=1000.0, skip_freshness=True)
    legacy = count_legacy_action_candidates(matches)
    current = count_current_work_orders(candidates)

    print("=== SQE-V1 Dry-Run Diag ===")
    print(f"birlesik={stats.birlesik} | legacy_action={legacy} | current_total={current['total']}")
    print(
        f"watch={current['watch']} | action={current['action']} | "
        f"high={current['high']} | actionable={current['actionable']}"
    )
    for item in candidates:
        print(
            f"  {item.tier} | {item.match_name} | EV=%{item.ev_percent} | stake={item.stake} TL"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQE-V1 feed and is emri diagnostigi")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mock feed ile test (Playwright/API gerektirmez)",
    )
    args = parser.parse_args(argv)

    try:
        if args.dry_run:
            return _run_dry_diag()
        return _run_live_diag()
    except KeyboardInterrupt:
        print("Iptal edildi.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

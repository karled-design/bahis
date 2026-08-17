#!/usr/bin/env python3
"""football-data.co.uk gecmis oranlariyla EV esigi backtest'i (0 API kredisi).

Kullanim:
    python tools/kapanis_backtest.py --ligler T1,E0 --sezonlar 2425,2324
    python tools/kapanis_backtest.py --kip clv --esikler 0.01,0.022,0.05
    python tools/kapanis_backtest.py --json

Veri kamuya aciktir ve ucretsizdir; indirilen CSV'ler
``database/backtest_cache/`` altinda saklanir, ayni sezon tekrar indirilmez.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.closing_backtest import (
    DEFAULT_THRESHOLDS,
    SelectionOutcome,
    parse_football_data_csv,
    run_closing_backtest,
)

_CSV_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
_CACHE_DIR = Path(__file__).resolve().parent.parent / "database" / "backtest_cache"
_DEFAULT_LEAGUES = ("T1", "E0", "D1", "SP1", "I1", "F1")
_DEFAULT_SEASONS = ("2425",)
_TIMEOUT_SECONDS = 30
_MAX_CSV_BYTES = 16_000_000


def _download_csv(league: str, season: str, *, cache_dir: Path) -> str | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{season}_{league}.csv"
    if cached.is_file():
        return cached.read_text(encoding="utf-8", errors="replace")

    url = _CSV_URL.format(season=season, league=league)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
            payload = response.read(_MAX_CSV_BYTES)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"indirilemedi | {url} | {exc}", file=sys.stderr)
        return None

    text = payload.decode("utf-8", errors="replace")
    cached.write_text(text, encoding="utf-8")
    return text


def _collect_selections(
    leagues: list[str],
    seasons: list[str],
    *,
    mode: str,
    cache_dir: Path,
) -> list[SelectionOutcome]:
    selections: list[SelectionOutcome] = []
    for season in seasons:
        for league in leagues:
            text = _download_csv(league, season, cache_dir=cache_dir)
            if not text:
                continue
            parsed = parse_football_data_csv(text, mode="clv" if mode == "clv" else "live")
            print(f"[backtest] {season}/{league} | secim={len(parsed)}", file=sys.stderr)
            selections.extend(parsed)
    return selections


def _format_report(report: dict[str, Any]) -> str:
    lines = [
        "=== KAPANIS BACKTEST ===",
        f"kip={report['kip']} | aday secim={report['aday_secim']}",
        "",
        f"{'esik':>8} {'bahis':>7} {'roi':>9} {'ort_ev':>8} {'ort_clv':>9} {'poz_clv':>8}",
    ]
    for threshold, summary in report["esikler"].items():
        lines.append(
            f"{threshold:>8} {summary['bahis']:>7} {summary['roi']:>9.4f} "
            f"{summary['ort_ev']:>8.4f} {summary['ort_clv']:>9.4f} "
            f"{summary['pozitif_clv_orani']:>8.4f}"
        )

    for title, key in (("LIG", "lig_kirilimi"), ("PAZAR", "pazar_kirilimi")):
        lines.append("")
        lines.append(f"--- {title} KIRILIMI (esik={report['kirilim_esigi']}) ---")
        for name, summary in report[key].items():
            lines.append(
                f"  {name:<12} bahis={summary['bahis']:<5} roi={summary['roi']:>8.4f} "
                f"ort_clv={summary['ort_clv']:>8.4f}"
            )

    lines.append("")
    lines.append(f"NOT: {report['not']}")
    lines.append(
        "NOT: Gecmis veri sonucu canlida kanitlanmis edge degildir; soft taraf "
        "B365 acilis orani, Nesine'nin vekilidir."
    )
    return "\n".join(lines)


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="football-data.co.uk EV esigi backtest'i")
    parser.add_argument("--ligler", default=",".join(_DEFAULT_LEAGUES))
    parser.add_argument("--sezonlar", default=",".join(_DEFAULT_SEASONS))
    parser.add_argument("--kip", choices=("live", "clv"), default="live")
    parser.add_argument(
        "--esikler",
        default=",".join(f"{value}" for value in DEFAULT_THRESHOLDS),
    )
    parser.add_argument("--kirilim-esigi", type=float, default=0.022)
    parser.add_argument("--cache-dir", type=Path, default=_CACHE_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        thresholds = [float(value) for value in _split(args.esikler)]
    except ValueError:
        print("--esikler sayisal olmali (ornek: 0.01,0.022,0.05)", file=sys.stderr)
        return 1

    selections = _collect_selections(
        _split(args.ligler),
        _split(args.sezonlar),
        mode=args.kip,
        cache_dir=args.cache_dir,
    )
    if not selections:
        print("secim uretilemedi (veri indirilemedi veya kolonlar eksik)", file=sys.stderr)
        return 1

    report = run_closing_backtest(
        selections,
        thresholds=thresholds,
        breakdown_threshold=args.kirilim_esigi,
        mode="clv" if args.kip == "clv" else "live",
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

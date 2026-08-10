#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from core.context_diag import (
    build_context_diag_report,
    format_context_diag_report,
    parse_commence_date,
    resolve_reference_date,
)
from database.db_manager import get_context_cache_stats, init_db
from scrapers.context_feed import enrich_match_feed_safely, is_api_football_configured

_DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "context" / "tur_super_lig_gs_fb.json"
)


def _load_fixture_matches(fixture_path: Path) -> list[dict]:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    match = dict(payload["match"])
    bundle = {
        "standings": payload.get("standings"),
        "home_form": payload.get("home_form"),
        "away_form": payload.get("away_form"),
        "h2h": payload.get("h2h"),
        "injuries": payload.get("injuries"),
    }
    rows: list[dict] = []
    for market, sharp_odds, soft_odds in (
        ("MS1", 2.05, 2.20),
        ("X", 3.40, 3.55),
        ("MS2", 3.80, 4.10),
    ):
        row = dict(match)
        row["market"] = market
        row["sharp_odds"] = sharp_odds
        row["soft_odds"] = soft_odds
        row["context_bundle"] = bundle
        row["consensus_books"] = 3
        rows.append(row)
    return rows


def _load_live_matches(*, enrich: bool) -> list[dict]:
    from scrapers.live_feed_gateway import get_unified_live_data

    matches = get_unified_live_data()
    if enrich and matches:
        matches = enrich_match_feed_safely(matches)
    return matches


def _resolve_cache_stats() -> dict[str, int]:
    try:
        init_db()
        return get_context_cache_stats()
    except Exception:
        return {}


def _resolve_reference_date_arg(source: str, fixture_path: Path, raw_date: str | None) -> date:
    if raw_date is not None and str(raw_date).strip():
        return resolve_reference_date(raw_date)
    if source == "fixture" and fixture_path.is_file():
        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            match = payload.get("match")
            if isinstance(match, dict):
                fixture_date = parse_commence_date(match)
                if fixture_date is not None:
                    return fixture_date
        except (OSError, json.JSONDecodeError):
            pass
    return resolve_reference_date(None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQE-V1 gunluk mac baglam/sk or raporu (Telegram yok)")
    parser.add_argument(
        "--source",
        choices=("live", "fixture"),
        default="fixture",
        help="Veri kaynagi (varsayilan: fixture, ag/API gerektirmez)",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=_DEFAULT_FIXTURE,
        help="Fixture JSON yolu (--source fixture)",
    )
    parser.add_argument(
        "--date",
        dest="reference_date",
        default=None,
        help="Rapor tarihi YYYY-MM-DD (varsayilan: bugun UTC)",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Canli feed icin API-Football baglam zenginlestirme (yalnizca --source live)",
    )
    parser.add_argument("--json", action="store_true", help="JSON cikti")
    args = parser.parse_args(argv)

    if args.enrich and args.source != "live":
        print("--enrich yalnizca --source live ile kullanilabilir.", file=sys.stderr)
        return 1

    if args.source == "fixture" and not args.fixture.is_file():
        print(f"Fixture dosyasi bulunamadi: {args.fixture}", file=sys.stderr)
        return 1

    if args.enrich and not is_api_football_configured():
        print("API_FOOTBALL_KEY yok; --enrich kullanilamaz.", file=sys.stderr)
        return 1

    try:
        reference_date = _resolve_reference_date_arg(
            args.source,
            args.fixture,
            args.reference_date,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        if args.source == "fixture":
            matches = _load_fixture_matches(args.fixture)
        else:
            matches = _load_live_matches(enrich=False)
            if args.enrich:
                matches = enrich_match_feed_safely(matches)

        report = build_context_diag_report(
            matches,
            reference_date=reference_date,
            cache_stats=_resolve_cache_stats(),
        )

        if args.json:
            payload = report.as_dict()
            if args.enrich:
                from scrapers.context_feed import get_last_context_feed_diag

                payload["context_feed"] = get_last_context_feed_diag()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_context_diag_report(report))
        return 0
    except KeyboardInterrupt:
        print("Iptal edildi.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Context diag basarisiz | {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

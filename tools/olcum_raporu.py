#!/usr/bin/env python3
"""Olcum modu konsol araci: mod ac/kapa, CLV yakala, karne yazdir.

Kullanim:
    python tools/olcum_raporu.py durum
    python tools/olcum_raporu.py ac
    python tools/olcum_raporu.py kapat
    python tools/olcum_raporu.py clv          # kickoff'u gecen sinyallerin CLV'si
    python tools/olcum_raporu.py rapor        # karne (metin)
    python tools/olcum_raporu.py rapor --json
"""
from __future__ import annotations

import argparse
import json
import sys

from core.measurement_mode import (
    capture_pending_clv,
    is_measurement_mode_enabled,
    report,
    set_measurement_mode,
)

_MIN_SAMPLE_DEFAULT = 5


def _format_summary(title: str, summary: dict[str, object], *, indent: str = "") -> str:
    return (
        f"{indent}{title:<24} sinyal={summary['sinyal']:<5} olculen={summary['olculen']:<5} "
        f"ort_CLV=%{summary['ort_clv_pct']:<7} pozitif=%{summary['pozitif_clv_orani']}"
    )


def _format_report(card: dict[str, object]) -> str:
    lines = ["=== OLCUM KARNESI ===", _format_summary("TOPLAM", card["toplam"])]  # type: ignore[index]
    for label, groups in card["kirilimlar"].items():  # type: ignore[index]
        lines.append(f"\n--- {label.upper()} ---")
        for key, summary in groups.items():
            suffix = "" if summary.get("yeterli_ornek") else "   (yetersiz ornek)"
            lines.append(_format_summary(key, summary, indent="  ") + suffix)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Olcum modu araci")
    subparsers = parser.add_subparsers(dest="komut", required=True)
    subparsers.add_parser("durum")
    subparsers.add_parser("ac")
    subparsers.add_parser("kapat")
    subparsers.add_parser("clv")
    report_parser = subparsers.add_parser("rapor")
    report_parser.add_argument("--json", action="store_true", dest="as_json")
    report_parser.add_argument("--min-ornek", type=int, default=_MIN_SAMPLE_DEFAULT)

    args = parser.parse_args(argv)

    if args.komut == "durum":
        print("Olcum modu: " + ("ACIK" if is_measurement_mode_enabled() else "KAPALI"))
        return 0

    if args.komut in {"ac", "kapat"}:
        enabled = args.komut == "ac"
        if not set_measurement_mode(enabled):
            print("Mod yazilamadi.", file=sys.stderr)
            return 1
        print("Olcum modu: " + ("ACIK (kupon acilmaz, bakiye degismez)" if enabled else "KAPALI"))
        return 0

    if args.komut == "clv":
        print(json.dumps(capture_pending_clv(), ensure_ascii=False, indent=2))
        return 0

    card = report(min_sample=args.min_ornek)
    print(json.dumps(card, ensure_ascii=False, indent=2) if args.as_json else _format_report(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

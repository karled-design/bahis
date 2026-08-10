"""Ilk yari (IY) sinyalleri icin golge kaydi (Asama B4).

Neden golge: IY sonucu pazari Nesine + keskin kaynakta VAR ve deger sinyali
uretilebilir; ANCAK sonuclandirma icin ilk-yari skoru gerekir ve mevcut skor
kaynagi (Odds API /scores) yalnizca mac sonu skorunu verir. Otomatik kapanamayan
kupon zombi olur. Bu yuzden IY sinyalleri Telegram'a GITMEZ, kupon ACMAZ;
yalnizca buraya yazilir ki ileride "IY degeri gercek mi?" sorusu veriyle
yanitlanabilsin (alt/ust ve konsensus icin izledigimiz kanit-once yolu).

Dosya: database/first_half_shadow.jsonl (her satir bir aday). Asla exception
firlatmaz — golge kaydi canli taramayi bozmamali.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ("record_first_half_shadow",)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SHADOW_PATH = _PROJECT_ROOT / "database" / "first_half_shadow.jsonl"
_LOCK = threading.Lock()


def record_first_half_shadow(candidates: list[Any], *, scan_time: str = "") -> int:
    """IY adaylarini golge dosyasina yazar; yazilan satir sayisini doner."""
    if not candidates:
        return 0
    lines: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        try:
            record = {
                "captured_at": now_iso,
                "scan_time": scan_time,
                "match_name": candidate.match_name,
                "market": candidate.market,
                "tier": candidate.tier,
                "ev_percent": candidate.ev_percent,
                "soft_odds": candidate.soft_odds,
                "sharp_odds": candidate.sharp_odds,
                "consensus_books": candidate.consensus_books,
                "league_name": candidate.league_name,
            }
            lines.append(json.dumps(record, ensure_ascii=False))
        except Exception as exc:  # tek aday bozuksa digerlerini engellemesin
            print(f"[SQE-V1] IY golge satiri atlandi | {exc}", file=sys.stderr)

    if not lines:
        return 0
    try:
        with _LOCK:
            _SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _SHADOW_PATH.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"[SQE-V1] IY golge dosyasi yazilamadi | {exc}", file=sys.stderr)
        return 0
    return len(lines)

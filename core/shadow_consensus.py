"""Asama 2 - Adim 1: konsensus ortalama yontemi golge (shadow) karsilastirmasi.

Bu modul CANLI KARARI ETKILEMEZ. Her taramada, keskin kitapcilarin oranlarini
iki farkli yontemle ortalayip yan yana bir deftere (JSONL) yazar:

  - old_odds  = oranlarin duz (aritmetik) ortalamasi  -> motorun BUGUN kullandigi
  - new_odds  = olasilik-uzayinda ortalama (1/oran'larin ortalamasi, sonra ters
                cevrilir; oranlarin harmonik ortalamasi)  -> ONERILEN yeni yontem

Duz ortalama, uzak ihtimalleri (yuksek oranlari) hafifce fazla degerli gosterir;
olasilik-uzayi ortalamasi bu yanliligi duzeltir. Hangisinin gercekten daha
isabetli oldugunu, birkac hafta veri toplandiktan sonra kapanis cizgisiyle (CLV)
kiyaslayarak karar verecegiz. Kanitlanmadan hicbir sey canliya alinmaz.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from config.settings import MIN_CONSENSUS_BOOKMAKERS

__all__ = ("record_consensus_shadow", "probspace_mean_odds", "decimal_mean_odds")

# Golge defteri: canli veritabaniyla ayni klasorde, ayri bir JSONL dosyasi.
_SHADOW_PATH = Path(__file__).resolve().parent.parent / "database" / "shadow_consensus.jsonl"


def decimal_mean_odds(prices: list[float]) -> float | None:
    """Eski (canli) yontem: oranlarin duz aritmetik ortalamasi."""
    if not prices:
        return None
    return sum(prices) / len(prices)


def probspace_mean_odds(prices: list[float]) -> float | None:
    """Yeni yontem: once 1/oran (ortuk sans) ortalanir, sonra ters cevrilir.

    Bu, oranlarin harmonik ortalamasidir; duz ortalamadan kucuk-esit cikar,
    yani ortuk sansi biraz yukari ceker (uzak-ihtimal yanliligini kisar).
    """
    if not prices:
        return None
    inverse_mean = sum(1.0 / price for price in prices) / len(prices)
    if inverse_mean <= 0.0:
        return None
    return 1.0 / inverse_mean


def _build_shadow_rows(events: list[Any], sport_key: str) -> list[dict[str, Any]]:
    # Canli kovalama mantiginin AYNISI (tek kaynak) — kayma olmaz.
    from scrapers.sharp_feed import _collect_price_buckets

    price_buckets, match_names, event_meta = _collect_price_buckets(
        events, sport_key=sport_key
    )
    min_books = max(1, int(MIN_CONSENSUS_BOOKMAKERS))
    observed_at = round(time.time(), 3)

    rows: list[dict[str, Any]] = []
    for bucket_key, bucket in price_buckets.items():
        event_key = bucket_key.split(":", 1)[0]
        match_name = match_names.get(event_key)
        if not match_name:
            continue

        sharp_prices = list(bucket.get("sharp") or [])
        all_prices = list(bucket.get("all") or [])
        use_sharp = len(sharp_prices) >= min_books
        prices = sharp_prices if use_sharp else all_prices
        if len(prices) < min_books:
            continue

        old_odds = decimal_mean_odds(prices)
        new_odds = probspace_mean_odds(prices)
        if old_odds is None or new_odds is None:
            continue

        meta = event_meta.get(event_key, {})
        rows.append(
            {
                "ts": observed_at,
                "sport_key": meta.get("sport_key", sport_key),
                "event_id": meta.get("event_id", event_key),
                "match": match_name,
                "market": bucket_key.split(":", 1)[1],
                "commence_time": meta.get("commence_time", ""),
                "n_all": len(all_prices),
                "n_sharp": len(sharp_prices),
                "source": "sharp_consensus" if use_sharp else "market_consensus",
                "old_odds": round(old_odds, 4),
                "new_odds": round(new_odds, 4),
            }
        )
    return rows


def _append_rows(rows: list[dict[str, Any]]) -> None:
    try:
        _SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _SHADOW_PATH.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[SQE-V1] golge defteri yazilamadi | {exc}", file=sys.stderr)


def record_consensus_shadow(events: list[Any], sport_key: str) -> int:
    """Bir taramanin iki-yontemli konsensusunu deftere yazar; yazilan satiri dondurur.

    CANLI KARARI ETKILEMEZ. Cagiran taraf zaten try/except ile sarar; yine de
    burada da bos/gecersiz girdiye karsi sessizce 0 doneriz.
    """
    if not isinstance(events, list) or not events:
        return 0
    rows = _build_shadow_rows(events, sport_key)
    if not rows:
        return 0
    _append_rows(rows)
    return len(rows)

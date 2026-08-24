"""Haber tetikleyicisi: fiyat hareketinin arkasinda taze bir haber var mi?

Steam avcisi "keskin fiyat kosarken Nesine yerinde kaldi" anini yakalar ama
hareketin SEBEBINI bilmez; sebep bir bulten (ilk 11, sakatlik, transfer) ise
Nesine'nin gecikmesi tesadufi degil yapisaldir ve pencere olculebilir.

Bu modul yeni istek atmaz: tarama turunun zaten diske yazdigi steam sinyallerini
ve mac kayitlarina eklenmis RSS haberlerini (`experimental_news`) birlestirir,
yani API kredisi HARCAMAZ.

Uretilen sinyal her zaman izleme (no-play) niteligindedir: kupon acmaz, tutar
onermez. Olcum defterine ayri katmanla (HABER) yazilir; boylece "haberle
dogrulanmis steam" ile "sebebi bilinmeyen steam" CLV'leri ayri ayri gorulur --
yontemin degeri kanitlanana kadar para riski alinmaz.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.steam_detector import SteamSignal

__all__ = (
    "NEWS_TIER",
    "NewsSignal",
    "build_news_message",
    "split_news_confirmed",
)

# Sinyal katmani: olcum defterinde sade steam sinyalinden ayrilir.
NEWS_TIER = "HABER"

# Haber bu yastan eskiyse fiyat hareketinin sebebi sayilmaz.
MAX_NEWS_AGE_SECONDS = 3.0 * 3600.0

_OPERATOR_DIAG = "Donanim Erisilemiyor: Haber Tetikleyici Hatasi"


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


@dataclass(frozen=True)
class NewsSignal:
    """Taze haberle dogrulanmis fiyat hareketi (her zaman izleme)."""

    steam: SteamSignal
    headline: str
    source: str
    news_age_minutes: float


def _parse_published_at(raw: str) -> float | None:
    value = raw.strip()
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _fresh_hit(match: dict[str, Any], *, now: float) -> tuple[str, str, float] | None:
    """Mac kaydindaki en taze RSS basligi: (baslik, kaynak, yas_dakika)."""
    news = match.get("experimental_news")
    if not isinstance(news, dict):
        return None
    hits = news.get("rss_hits")
    if not isinstance(hits, list):
        return None

    freshest: tuple[str, str, float] | None = None
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        title = str(hit.get("title", "")).strip()
        if not title:
            continue
        published = _parse_published_at(str(hit.get("published_at", "")))
        if published is None:
            continue
        age = now - published
        if age < 0.0 or age > MAX_NEWS_AGE_SECONDS:
            continue
        if freshest is None or age < freshest[2]:
            freshest = (title, str(hit.get("source", "")).strip(), age)

    if freshest is None:
        return None
    return (freshest[0], freshest[1], freshest[2] / 60.0)


def _match_index(matches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        name = str(match.get("match_name", "")).strip().casefold()
        if name and name not in index:
            index[name] = match
    return index


def split_news_confirmed(
    signals: list[SteamSignal],
    matches: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> tuple[list[NewsSignal], list[SteamSignal]]:
    """Steam sinyallerini haberli / habersiz olarak ikiye ayirir."""
    reference_now = time.time() if now is None else float(now)
    try:
        index = _match_index(matches)
    except (AttributeError, TypeError) as exc:
        _emit_operator_diag(f"mac dizini kurulamadi | {exc}")
        return ([], list(signals))

    confirmed: list[NewsSignal] = []
    plain: list[SteamSignal] = []
    for signal in signals:
        match = index.get(signal.match_name.strip().casefold())
        hit = _fresh_hit(match, now=reference_now) if match is not None else None
        if hit is None:
            plain.append(signal)
            continue
        headline, source, age_minutes = hit
        confirmed.append(
            NewsSignal(
                steam=signal,
                headline=headline,
                source=source,
                news_age_minutes=round(age_minutes, 1),
            )
        )
    return (confirmed, plain)


def build_news_message(signal: NewsSignal) -> str:
    """Telegram metni. 'Oyna' dugmesi yok: bu bir izleme bildirimidir."""
    steam = signal.steam
    return (
        "📰 HABER + FIYAT HAREKETI | IZLE\n"
        f"Mac: {steam.match_name}\n"
        f"Pazar: {steam.market}\n"
        f"Lig: {steam.league_name or 'Bilinmiyor'}\n"
        f"Keskin oran: {steam.onceki_sharp:.2f} -> {steam.sharp_odds:.2f} "
        f"(%{steam.sharp_drop * 100.0:.1f} dustu, {steam.gozlem_araligi_dk:.0f} dk)\n"
        f"Nesine: {steam.soft_odds:.2f} (hareketi izlemedi)\n"
        f"Haber ({signal.news_age_minutes:.0f} dk once, {signal.source or 'RSS'}): "
        f"{signal.headline}\n"
        "Not: Olcum sinyali — kupon acilmaz; haber pazarda fiyatlanana kadar "
        "pencere dakikalarla olculur."
    )

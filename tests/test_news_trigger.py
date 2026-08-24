"""Haber tetikleyicisi: fiyat hareketini taze haberle dogrulama katmani."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.news_trigger import NEWS_TIER, build_news_message, split_news_confirmed
from core.steam_detector import SteamSignal

NOW = 1_800_000_000.0


def _published(minutes_ago: float) -> str:
    moment = datetime.fromtimestamp(NOW, tz=timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _steam(match_name: str = "Arsenal - Chelsea") -> SteamSignal:
    return SteamSignal(
        match_name=match_name,
        market="MS1",
        league_name="Premier League",
        sport_key="soccer_epl",
        event_id="evt-1",
        commence_time="2027-01-01T18:00:00Z",
        consensus_source="exchange",
        consensus_books=3,
        onceki_sharp=2.10,
        sharp_odds=1.95,
        soft_odds=2.15,
        sharp_drop=0.071,
        soft_drift=0.0,
        gozlem_araligi_dk=35.0,
    )


def _match(
    match_name: str = "Arsenal - Chelsea",
    *,
    minutes_ago: float = 20.0,
    title: str = "Arsenal team news: two starters ruled out",
    with_news: bool = True,
) -> dict[str, Any]:
    record: dict[str, Any] = {"match_name": match_name, "market": "MS1"}
    if with_news:
        record["experimental_news"] = {
            "rss_hits": [
                {
                    "title": title,
                    "source": "BBC Football",
                    "published_at": _published(minutes_ago),
                    "teams": ["arsenal", "chelsea"],
                }
            ]
        }
    return record


def test_taze_haber_sinyali_haber_katmanina_alir() -> None:
    confirmed, plain = split_news_confirmed([_steam()], [_match()], now=NOW)

    assert plain == []
    assert len(confirmed) == 1
    assert confirmed[0].headline.startswith("Arsenal team news")
    assert confirmed[0].source == "BBC Football"
    assert confirmed[0].news_age_minutes == 20.0


def test_habersiz_sinyal_steam_olarak_kalir() -> None:
    signal = _steam()

    confirmed, plain = split_news_confirmed([signal], [_match(with_news=False)], now=NOW)

    assert confirmed == []
    assert plain == [signal]


def test_eski_haber_sinyali_yukseltmez() -> None:
    signal = _steam()

    confirmed, plain = split_news_confirmed([signal], [_match(minutes_ago=400.0)], now=NOW)

    assert confirmed == []
    assert plain == [signal]


def test_baska_macin_haberi_kullanilmaz() -> None:
    signal = _steam()

    confirmed, plain = split_news_confirmed([signal], [_match("Leeds - Everton")], now=NOW)

    assert confirmed == []
    assert plain == [signal]


def test_en_taze_haber_secilir() -> None:
    match = _match(minutes_ago=90.0)
    match["experimental_news"]["rss_hits"].append(
        {
            "title": "Chelsea confirm starting XI",
            "source": "Sky Sports Football",
            "published_at": _published(5.0),
            "teams": ["chelsea"],
        }
    )

    confirmed, _ = split_news_confirmed([_steam()], [match], now=NOW)

    assert confirmed[0].headline == "Chelsea confirm starting XI"
    assert confirmed[0].news_age_minutes == 5.0


def test_bozuk_haber_kaydi_sinyali_dusurmez() -> None:
    match = _match()
    match["experimental_news"]["rss_hits"] = [{"title": "", "published_at": "gecersiz"}]
    signal = _steam()

    confirmed, plain = split_news_confirmed([signal], [match], now=NOW)

    assert confirmed == []
    assert plain == [signal]


def test_mesaj_kupon_acilmadigini_ve_haberi_soyler() -> None:
    confirmed, _ = split_news_confirmed([_steam()], [_match()], now=NOW)
    message = build_news_message(confirmed[0])

    assert "kupon acilmaz" in message
    assert "Arsenal team news" in message
    assert "IZLE" in message


def test_katman_etiketi_steamden_ayridir() -> None:
    assert NEWS_TIER == "HABER"

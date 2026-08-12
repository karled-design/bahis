"""Referans fiyat kalitesi: hangi kitapci nereden sayilir, hangi kayit bahse girer.

Nesine'nin fiyat hatasini olcebilmek icin referansin **keskin** olmasi sart.
Pinnacle ve borsalar (Betfair Exchange, Matchbook, Smarkets) fiyatlarini akilli
paranin baskisiyla duzeltir; bet365 / Unibet / Betfair *sportsbook* ise Nesine
ile ayni sinifta yumusak kitapcidir. Ikisi ayni torbaya konursa "referans" olarak
kullandigimiz sey, olcmeye calistigimiz hatanin bir kopyasi olur.

Bu modul referansi UC kaliteye ayirir ve her kaliteye izin verilen en yuksek
sinyal katmanini (tier) baglar:

    pinnacle / exchange_consensus -> ACTION + HIGH (tam yetki)
    sharp_consensus (ikincil)     -> yalnizca WATCH (izleme)
    market_consensus              -> bildirim YOK (panelde gorunur, kupon acmaz)
"""

from __future__ import annotations

__all__ = (
    "EXCHANGE_BOOKMAKERS",
    "PINNACLE_KEY",
    "REFERENCE_EXCHANGE",
    "REFERENCE_MARKET",
    "REFERENCE_PINNACLE",
    "REFERENCE_SECONDARY",
    "SECONDARY_SHARP_BOOKMAKERS",
    "is_notifiable_reference",
    "max_tier_for_reference",
    "probability_space_mean_odds",
)

PINNACLE_KEY = "pinnacle"

# Borsa: komisyon oncesi fiyat, akilli paranin dogrudan olustur.
EXCHANGE_BOOKMAKERS = frozenset(
    {
        "betfair_ex_uk",
        "betfair_ex_eu",
        "betfair_ex_au",
        "matchbook",
        "smarkets",
    }
)

# Ikincil "keskin sayilan" kitapcilar: aslinda yumusak, ama Pinnacle/borsa
# yokken piyasa fikri verir. Bunlarla uretilen fark bahse girmez, izlenir.
SECONDARY_SHARP_BOOKMAKERS = frozenset(
    {
        "bet365",
        "unibet_uk",
        "betfair_sb_uk",
        "williamhill",
        "betvictor",
    }
)

REFERENCE_PINNACLE = "pinnacle"
REFERENCE_EXCHANGE = "exchange_consensus"
REFERENCE_SECONDARY = "sharp_consensus"
REFERENCE_MARKET = "market_consensus"

_FULL_TIER_SOURCES = frozenset({REFERENCE_PINNACLE, REFERENCE_EXCHANGE})
_WATCH_ONLY_SOURCES = frozenset({REFERENCE_SECONDARY})


def probability_space_mean_odds(prices: list[float]) -> float | None:
    """Oranlari OLASILIK uzayinda ortalar (1/oran'larin ortalamasi, sonra ters).

    Oranlarin duz aritmetik ortalamasi ortuk sansi sistematik olarak asagi
    ceker (Jensen esitsizligi) ve uzak ihtimalleri fazla degerli gosterir;
    olasilik uzayinda ortalama bu yanliligi tasimaz.
    """
    valid = [float(price) for price in prices if isinstance(price, (int, float)) and not isinstance(price, bool) and float(price) > 1.0]
    if not valid:
        return None
    inverse_mean = sum(1.0 / price for price in valid) / len(valid)
    if inverse_mean <= 0.0:
        return None
    return 1.0 / inverse_mean


def max_tier_for_reference(source: str) -> str | None:
    """Bu referans kalitesiyle uretilebilecek en yuksek katman.

    None -> bu referansla bildirim gonderilmez.
    """
    key = str(source or "").strip().casefold()
    if key in _FULL_TIER_SOURCES:
        return "HIGH"
    if key in _WATCH_ONLY_SOURCES:
        return "WATCH"
    return None


def is_notifiable_reference(source: str) -> bool:
    return max_tier_for_reference(source) is not None

from __future__ import annotations

import math

__all__ = (
    "FAMILY_BTTS",
    "FAMILY_FIRST_HALF",
    "FAMILY_MATCH_RESULT",
    "FAMILY_TOTALS",
    "family_outcome_count",
    "format_totals_line",
    "market_family",
    "market_family_group_key",
    "parse_totals_market",
    "totals_market_key",
)

# Pazar aileleri: ayni macta birbiriyle yarisan secenek kumeleri.
# Mac sonucu (MS1/X/MS2) bir aile; toplam gol alt/ust cizgi basina ayri aile.
# Yan pazarlar (Asama B): karsilikli gol (KG VAR/KG YOK) ve ilk yari sonucu
# (IY1/IYX/IY2) — Nesine MTID teshisi docs/nesine-yan-pazar-kesif-20260712.md.
FAMILY_MATCH_RESULT = "mac_sonucu"
FAMILY_TOTALS = "toplam_gol"
FAMILY_BTTS = "karsilikli_gol"
FAMILY_FIRST_HALF = "ilk_yari_sonucu"

_MATCH_RESULT_MARKETS = frozenset({"MS1", "MS2", "X"})
_TOTALS_PREFIXES = ("ALT ", "UST ")
_BTTS_MARKETS = frozenset({"KG VAR", "KG YOK"})
_FIRST_HALF_MARKETS = frozenset({"IY1", "IYX", "IY2"})


def market_family(market: str) -> str | None:
    key = str(market or "").strip().upper()
    if key in _MATCH_RESULT_MARKETS:
        return FAMILY_MATCH_RESULT
    if key.startswith(_TOTALS_PREFIXES):
        return FAMILY_TOTALS
    if key in _BTTS_MARKETS:
        return FAMILY_BTTS
    if key in _FIRST_HALF_MARKETS:
        return FAMILY_FIRST_HALF
    return None


def market_family_group_key(market: str) -> str | None:
    """Devig ve ustunluk kiyasinda ayni gruba giren pazarlarin anahtari.

    Mac sonucu tek grup; alt/ust cizgi bazinda gruplanir (ALT 2.5 + UST 2.5).
    Taninmayan pazar None doner; o kayit gruplamaya girmez.
    """
    family = market_family(market)
    if family == FAMILY_MATCH_RESULT:
        return FAMILY_MATCH_RESULT
    if family == FAMILY_TOTALS:
        parts = str(market).strip().upper().split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            return f"{FAMILY_TOTALS}:{parts[1].strip()}"
    if family == FAMILY_BTTS:
        return FAMILY_BTTS
    if family == FAMILY_FIRST_HALF:
        return FAMILY_FIRST_HALF
    return None


def family_outcome_count(group_key: str) -> int | None:
    """Grubun tam sayilmasi icin gereken sonuc adedi (marj temizligi kosulu)."""
    if group_key == FAMILY_MATCH_RESULT:
        return 3
    if group_key.startswith(f"{FAMILY_TOTALS}:"):
        return 2
    if group_key == FAMILY_BTTS:
        return 2
    if group_key == FAMILY_FIRST_HALF:
        return 3
    return None


# Toplam gol cizgisi icin akla yatkin band; disindaki degerler veri hatasidir.
_TOTALS_LINE_MIN = 0.25
_TOTALS_LINE_MAX = 10.0


def format_totals_line(value: object) -> str | None:
    """Cizgi degerini tek bicime indirger: 2.5 -> "2.5", 3.0 -> "3", "2,5" -> "2.5".

    Iki taraf (Nesine + kuresel kaynak) ayni metni uretmezse eslesme olmaz;
    bu yuzden bicimleme TEK burada yapilir.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(numeric) or not (_TOTALS_LINE_MIN <= numeric <= _TOTALS_LINE_MAX):
        return None
    return "%g" % numeric


def totals_market_key(side: str, line: object) -> str | None:
    """Alt/ust pazar anahtari uretir: ("UST", 2.5) -> "UST 2.5"."""
    side_key = str(side or "").strip().upper()
    if side_key not in {"ALT", "UST"}:
        return None
    line_text = format_totals_line(line)
    if line_text is None:
        return None
    return f"{side_key} {line_text}"


def parse_totals_market(market: str) -> tuple[str, float] | None:
    """"ALT 2.5" -> ("ALT", 2.5); alt/ust olmayan pazarlar icin None."""
    parts = str(market or "").strip().upper().split(" ", 1)
    if len(parts) != 2 or parts[0] not in {"ALT", "UST"}:
        return None
    try:
        line = float(parts[1].strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(line) or not (_TOTALS_LINE_MIN <= line <= _TOTALS_LINE_MAX):
        return None
    return parts[0], line

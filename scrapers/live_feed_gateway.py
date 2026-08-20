from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, TypedDict

from scrapers.matchbook_feed import get_matchbook_sharp_odds
from scrapers.sharp_feed import (
    check_odds_api_health,
    fetch_settlement_results,
    get_last_sharp_scan_diag,
    get_sharp_live_odds,
)
from scrapers.soft_feed import get_yasal_live_odds

from core.match_filters import (
    is_feed_pair_synchronized,
    is_feed_timestamp_fresh,
    is_suspicious_odds_pair,
    is_virtual_match_text,
)

__all__ = ("get_unified_live_data", "get_settlement_feed", "check_live_feed_health", "get_dry_run_live_data", "set_dry_run_mode")

_OPERATOR_DIAG = "Donanim Erisilemiyor: Canli Veri Gateway Hatasi"
_DRY_RUN_MODE = False
_MATCH_RATIO_THRESHOLD = 0.70
# Iki taraf da ayni baslama saatini bildiriyorsa isim benzerligi tek basina karar
# vermez: kickoff dogrulamasi cok daha guclu bir kimliktir, bu yuzden esik duser.
# ("Avai FC - Sport Club do Recife" <-> "Avai SC - Recife" gibi eslesmeler icin.)
_KICKOFF_VERIFIED_RATIO_THRESHOLD = 0.50
# Iki kaynagin bildirdigi baslama saati bu kadar ayrilabilir; fazlasi baska mactir.
_KICKOFF_TOLERANCE_SECONDS = 15.0 * 60.0
# Kulup adlarindaki jenerik ekler: kaynaklar bunlari tutarsiz yazar.
_CLUB_AFFIX_TOKENS = frozenset(
    {
        "fc", "cf", "afc", "sc", "ec", "ac", "as", "rc", "cd", "sd", "ud", "us",
        "ss", "ssc", "bsc", "sv", "sk", "fk", "bk", "ik", "if", "nk", "hnk",
        "gnk", "kv", "kaa", "rcd", "sco", "osc", "fr", "ca", "cs", "club",
        "calcio", "spor", "kulubu",
    }
)
# Bildirimlerin uretilecegi evre: sadece "prematch" (mac oncesi) eslesmeler gecer.
# Iki tarafin da (Nesine + referans) bu evrede olmasi sart; canli ve karisik eslesmeler elenir.
_REQUIRED_FEED_PHASE = "prematch"
# En fazla kac gun ilerisi bildirim olabilir. Nesine maclari ancak ~1 hafta oncesinden
# satisa acar; daha uzaktaki maclar bultende olmadigi icin oynanamaz, bildirim de uretilmez.
_MAX_PREMATCH_HORIZON_DAYS = 7
_ASCII_TRANSLATION = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
_TEAM_NAME_CANONICAL: dict[str, str] = {
    "argentina": "argentina",
    "arjantin": "argentina",
    "mexico": "mexico",
    "meksika": "mexico",
    "germany": "germany",
    "almanya": "germany",
    "france": "france",
    "fransa": "france",
    "england": "england",
    "ingiltere": "england",
    "spain": "spain",
    "ispanya": "spain",
    "italy": "italy",
    "italya": "italy",
    "brazil": "brazil",
    "brezilya": "brazil",
    "portugal": "portugal",
    "portekiz": "portugal",
    "netherlands": "netherlands",
    "hollanda": "netherlands",
    "belgium": "belgium",
    "belcika": "belgium",
    "croatia": "croatia",
    "hirvatistan": "croatia",
    "switzerland": "switzerland",
    "isvicre": "switzerland",
    "usa": "usa",
    "abd": "usa",
    "united states": "usa",
    "amerika": "usa",
    "canada": "canada",
    "kanada": "canada",
    "japan": "japan",
    "japonya": "japan",
    "south korea": "south korea",
    "guney kore": "south korea",
    "kore": "south korea",
    "morocco": "morocco",
    "fas": "morocco",
    "senegal": "senegal",
    "poland": "poland",
    "polonya": "poland",
    "sweden": "sweden",
    "isvec": "sweden",
    "norway": "norway",
    "norvec": "norway",
    "denmark": "denmark",
    "danimarka": "denmark",
    "wales": "wales",
    "galler": "wales",
    "montenegro": "montenegro",
    "karadag": "montenegro",
    "turkiye": "turkey",
    "turkey": "turkey",
    "türkiye": "turkey",
    "jordan": "jordan",
    "urdun": "jordan",
    "ürdün": "jordan",
    "draw": "draw",
    "beraberlik": "draw",
    "bayern munich": "bayern munich",
    "bayern munih": "bayern munich",
    "borussia dortmund": "borussia dortmund",
}


class UnifiedMatchFeed(TypedDict, total=False):
    match_name: str
    market: str
    sharp_odds: float
    soft_odds: float
    league_name: str
    observed_at: float
    soft_observed_at: float
    sharp_observed_at: float
    consensus_books: int
    consensus_source: str
    event_id: str
    commence_time: str
    sport_key: str
    match_quality: str
    fair_probability: float
    market_overround: float


class SettlementFeedEntry(TypedDict):
    match_name: str
    market: str
    outcome: str


_LEAGUE_FALLBACK = "Bilinmiyor"
_NESINE_SOFT_SOURCE = "nesine"
_SHARP_METADATA_KEYS = ("event_id", "commence_time", "sport_key", "league_name")
_LEAGUE_FIELD_KEYS = (
    "league_name",
    "competition",
    "tournament",
    "comp_name",
    "lig",
    "league",
    "competition_name",
    "tournament_name",
    "sport_title",
)


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _emit_scan_info(message: str) -> None:
    print(f"[SQE-V1] Piyasa Taraması: {message}")


def _extract_league_name(raw_packet: Any) -> str:
    if not isinstance(raw_packet, dict):
        return _LEAGUE_FALLBACK

    for key in _LEAGUE_FIELD_KEYS:
        try:
            value = raw_packet.get(key)
        except (AttributeError, TypeError):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested_key in ("name", "title", "label"):
                nested_value = value.get(nested_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()

    return _LEAGUE_FALLBACK


def _normalize_match_name_for_fuzzy(match_name: str) -> str:
    normalized = match_name.strip().lower().translate(_ASCII_TRANSLATION)
    normalized = re.sub(r"\([^)]*\)", " ", normalized)
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _canonicalize_team_name(team_name: str) -> str:
    normalized = _normalize_match_name_for_fuzzy(team_name)
    if not normalized:
        return normalized

    if normalized in _TEAM_NAME_CANONICAL:
        return _TEAM_NAME_CANONICAL[normalized]

    tokens = [_TEAM_NAME_CANONICAL.get(token, token) for token in normalized.split()]
    stripped = [token for token in tokens if token not in _CLUB_AFFIX_TOKENS]
    # Ad tamamen jenerik eklerden olusuyorsa (orn. "AC") ekler ayirt edicidir.
    canonical = " ".join(stripped or tokens)
    return _TEAM_NAME_CANONICAL.get(canonical, canonical)


def _split_match_teams(match_name: str) -> tuple[str, str]:
    if " - " in match_name:
        home_team, away_team = match_name.split(" - ", 1)
        return home_team.strip(), away_team.strip()
    return match_name.strip(), ""


def _sequence_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _token_containment(left: str, right: str) -> float:
    """Kisa adin uzun adin icinde gecme orani.

    Kaynaklar ayni takimi farkli uzunlukta yazar ("Recife" <-> "Sport Club do
    Recife"). Karakter dizisi benzerligi bu durumda dusuk kalir, oysa kisa adin
    tum sozcukleri uzun adin icindeyse kimlik nettir.
    """
    left_tokens = {token for token in left.split() if len(token) > 2}
    right_tokens = {token for token in right.split() if len(token) > 2}
    if not left_tokens or not right_tokens:
        return 0.0
    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    return len(shorter & longer) / len(shorter)


def _parse_kickoff_epoch(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        kickoff = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return kickoff.timestamp()


def _kickoff_relation(soft_entry: object, sharp_entry: object) -> str:
    """Iki kaydin baslama saati iliskisi: 'ayni', 'farkli' veya 'bilinmiyor'."""
    if not isinstance(soft_entry, dict) or not isinstance(sharp_entry, dict):
        return "bilinmiyor"
    soft_kickoff = _parse_kickoff_epoch(soft_entry.get("commence_time"))
    sharp_kickoff = _parse_kickoff_epoch(sharp_entry.get("commence_time"))
    if soft_kickoff is None or sharp_kickoff is None:
        return "bilinmiyor"
    if abs(soft_kickoff - sharp_kickoff) <= _KICKOFF_TOLERANCE_SECONDS:
        return "ayni"
    return "farkli"


def _match_names_fuzzy(
    soft_name: str, sharp_name: str, *, allow_containment: bool = False
) -> float:
    soft_full = _canonicalize_team_name(soft_name)
    sharp_full = _canonicalize_team_name(sharp_name)
    full_ratio = _sequence_ratio(soft_full, sharp_full)

    soft_home, soft_away = _split_match_teams(soft_name)
    sharp_home, sharp_away = _split_match_teams(sharp_name)
    soft_home_norm = _canonicalize_team_name(soft_home)
    soft_away_norm = _canonicalize_team_name(soft_away)
    sharp_home_norm = _canonicalize_team_name(sharp_home)
    sharp_away_norm = _canonicalize_team_name(sharp_away)

    if soft_home_norm and soft_away_norm and sharp_home_norm and sharp_away_norm:
        direct_ratio = (
            _sequence_ratio(soft_home_norm, sharp_home_norm)
            + _sequence_ratio(soft_away_norm, sharp_away_norm)
        ) / 2
        swapped_ratio = (
            _sequence_ratio(soft_home_norm, sharp_away_norm)
            + _sequence_ratio(soft_away_norm, sharp_home_norm)
        ) / 2
        team_ratio = max(direct_ratio, swapped_ratio)
        if allow_containment:
            # Kismi ad eslesmesi ancak baslama saati dogrulanmissa guvenlidir:
            # tek basina "Arsenal" ile "Arsenal Tula"yi ayni sayabilirdi.
            direct_containment = (
                _token_containment(soft_home_norm, sharp_home_norm)
                + _token_containment(soft_away_norm, sharp_away_norm)
            ) / 2
            swapped_containment = (
                _token_containment(soft_home_norm, sharp_away_norm)
                + _token_containment(soft_away_norm, sharp_home_norm)
            ) / 2
            team_ratio = max(team_ratio, direct_containment, swapped_containment)
        return max(full_ratio, team_ratio)

    return full_ratio


def _index_feed_by_market(
    feed: dict[str, dict[str, str | float]],
) -> dict[str, list[dict[str, str | float]]]:
    indexed: dict[str, list[dict[str, str | float]]] = {}
    for entry in feed.values():
        if not isinstance(entry, dict):
            continue
        match_name = entry.get("match_name")
        market = entry.get("market")
        if not isinstance(match_name, str) or not isinstance(market, str):
            continue
        if not match_name.strip() or not market.strip():
            continue
        market_key = market.strip().upper()
        indexed.setdefault(market_key, []).append(entry)
    return indexed


def _find_fuzzy_sharp_entry(
    soft_match_name: str,
    market: str,
    sharp_entries: list[dict[str, str | float]],
    used_sharp_ids: set[int],
    soft_entry: dict[str, str | float] | None = None,
) -> dict[str, str | float] | None:
    best_entry: dict[str, str | float] | None = None
    best_ratio = 0.0

    for sharp_entry in sharp_entries:
        sharp_entry_id = id(sharp_entry)
        if sharp_entry_id in used_sharp_ids:
            continue

        sharp_match_name = sharp_entry.get("match_name")
        if not isinstance(sharp_match_name, str) or not sharp_match_name.strip():
            continue

        kickoff_relation = _kickoff_relation(soft_entry, sharp_entry)
        if kickoff_relation == "farkli":
            # Isimler benzese de baska bir mac: yanlis eslesme fiyat farkini
            # tamamen uydurma yapar, bu yuzden aday listeden dusuruluyor.
            continue
        threshold = (
            _KICKOFF_VERIFIED_RATIO_THRESHOLD
            if kickoff_relation == "ayni"
            else _MATCH_RATIO_THRESHOLD
        )

        ratio = _match_names_fuzzy(
            soft_match_name,
            sharp_match_name,
            allow_containment=kickoff_relation == "ayni",
        )
        if ratio >= threshold and ratio > best_ratio:
            best_ratio = ratio
            best_entry = sharp_entry

    if best_entry is not None:
        used_sharp_ids.add(id(best_entry))

    return best_entry


def _resolve_unified_league_name(packet: dict[str, Any]) -> str:
    sharp_league = packet.get("league_name")
    if isinstance(sharp_league, str) and sharp_league.strip() and sharp_league.strip() != _LEAGUE_FALLBACK:
        return sharp_league.strip()
    return _extract_league_name(packet)


def _copy_sharp_metadata(raw_packet: dict[str, Any], sharp_entry: dict[str, str | float]) -> None:
    for key in _SHARP_METADATA_KEYS:
        value = sharp_entry.get(key)
        if isinstance(value, str) and value.strip():
            raw_packet[key] = value.strip()


def _build_unified_match(
    match_name: str,
    market: str,
    sharp_odds: float,
    soft_odds: float,
    raw_packet: dict[str, Any] | None = None,
) -> UnifiedMatchFeed:
    if sharp_odds <= 1.0 or soft_odds <= 1.0:
        raise ValueError("odds must be greater than 1.0")

    packet: dict[str, Any] = dict(raw_packet) if isinstance(raw_packet, dict) else {}
    packet.setdefault("match_name", match_name)
    packet.setdefault("market", market)

    unified: UnifiedMatchFeed = {
        "match_name": match_name.strip(),
        "market": market.strip(),
        "sharp_odds": round(float(sharp_odds), 2),
        "soft_odds": round(float(soft_odds), 2),
        "league_name": _resolve_unified_league_name(packet),
        "observed_at": float(packet.get("observed_at", 0.0)),
        "soft_observed_at": float(packet.get("soft_observed_at", 0.0)),
        "sharp_observed_at": float(packet.get("sharp_observed_at", 0.0)),
        "consensus_books": int(packet.get("consensus_books", 0) or 0),
    }

    for key in (
        "event_id",
        "commence_time",
        "sport_key",
        "soft_source",
        "feed_phase",
        # Referans kalitesi (pinnacle / exchange / ikincil / piyasa): karar
        # katmani bu etikete gore yetki verir, tasinmasi zorunlu.
        "consensus_source",
    ):
        value = packet.get(key)
        if isinstance(value, str) and value.strip():
            unified[key] = value.strip()

    # Marji temizlenmis olasilik (devig) varsa hesap katmanina tasinir.
    for numeric_key in ("fair_probability", "market_overround"):
        numeric_value = packet.get(numeric_key)
        if isinstance(numeric_value, (int, float)) and not isinstance(numeric_value, bool):
            unified[numeric_key] = float(numeric_value)

    if is_suspicious_odds_pair(float(soft_odds), float(sharp_odds)):
        unified["match_quality"] = "suspicious"
    else:
        unified["match_quality"] = "ok"

    return unified


def _is_within_betting_horizon(commence_time: str) -> bool:
    """Mac baslangici bugunden en fazla _MAX_PREMATCH_HORIZON_DAYS gun ileride mi?

    Nesine ancak ~1 hafta oncesini satar; daha uzaktakiler bultende olmaz, oynanamaz.
    Tarih okunamazsa guvenli tarafta kalip True donulur (mevcut evre kurallari zaten eler).
    """
    value = str(commence_time or "").strip()
    if not value:
        return True
    try:
        kickoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    gun_farki = (kickoff - datetime.now(timezone.utc)).total_seconds() / 86400.0
    return gun_farki <= _MAX_PREMATCH_HORIZON_DAYS


def _merge_live_feeds(
    soft_feed: dict[str, dict[str, str | float]],
    sharp_feed: dict[str, dict[str, str | float]],
    *,
    batch_epoch: float,
) -> list[dict[str, Any]]:
    unified: list[dict[str, Any]] = []
    sharp_by_market = _index_feed_by_market(sharp_feed)
    used_sharp_ids: set[int] = set()
    skipped_stale = 0
    skipped_phase = 0
    skipped_horizon = 0

    for soft_entry in soft_feed.values():
        if not isinstance(soft_entry, dict):
            continue

        match_name = soft_entry.get("match_name")
        market = soft_entry.get("market")
        soft_odds = soft_entry.get("soft_odds")
        if not isinstance(match_name, str) or not isinstance(market, str):
            continue
        if isinstance(soft_odds, bool) or not isinstance(soft_odds, (int, float)):
            continue

        soft_observed_at = float(soft_entry.get("observed_at", 0.0))
        if not is_feed_timestamp_fresh(soft_observed_at, reference_at=batch_epoch):
            skipped_stale += 1
            continue

        market_key = market.strip().upper()
        sharp_entry = _find_fuzzy_sharp_entry(
            match_name,
            market_key,
            sharp_by_market.get(market_key, []),
            used_sharp_ids,
            soft_entry,
        )
        if sharp_entry is None:
            continue

        sharp_odds = sharp_entry.get("sharp_odds")
        if isinstance(sharp_odds, bool) or not isinstance(sharp_odds, (int, float)):
            continue

        sharp_observed_at = float(sharp_entry.get("observed_at", 0.0))
        if not is_feed_timestamp_fresh(sharp_observed_at, reference_at=batch_epoch):
            skipped_stale += 1
            continue
        if not is_feed_pair_synchronized(soft_observed_at, sharp_observed_at):
            skipped_stale += 1
            continue

        soft_phase = str(soft_entry.get("feed_phase", "")).strip().lower()
        sharp_phase = str(sharp_entry.get("feed_phase", "")).strip().lower()
        if soft_phase != _REQUIRED_FEED_PHASE or sharp_phase != _REQUIRED_FEED_PHASE:
            skipped_phase += 1
            continue

        if not _is_within_betting_horizon(sharp_entry.get("commence_time", "")):
            skipped_horizon += 1
            continue

        sharp_match_name = sharp_entry.get("match_name")
        display_match_name = (
            sharp_match_name.strip()
            if isinstance(sharp_match_name, str) and sharp_match_name.strip()
            else match_name.strip()
        )

        if is_virtual_match_text(display_match_name, match_name, market):
            continue

        raw_packet = {
            "match_name": display_match_name,
            "market": market,
            "observed_at": batch_epoch,
            "soft_observed_at": soft_observed_at,
            "sharp_observed_at": sharp_observed_at,
            "feed_phase": sharp_phase,
        }
        consensus_books = sharp_entry.get("consensus_books")
        if isinstance(consensus_books, (int, float)) and not isinstance(consensus_books, bool):
            raw_packet["consensus_books"] = int(consensus_books)
        consensus_source = sharp_entry.get("consensus_source")
        if isinstance(consensus_source, str) and consensus_source.strip():
            raw_packet["consensus_source"] = consensus_source.strip()
        for numeric_key in ("fair_probability", "market_overround"):
            numeric_value = sharp_entry.get(numeric_key)
            if isinstance(numeric_value, (int, float)) and not isinstance(numeric_value, bool):
                raw_packet[numeric_key] = float(numeric_value)
        _copy_sharp_metadata(raw_packet, sharp_entry)
        soft_source = soft_entry.get("soft_source")
        if isinstance(soft_source, str) and soft_source.strip():
            raw_packet["soft_source"] = soft_source.strip().lower()
        for key in _LEAGUE_FIELD_KEYS:
            if key in soft_entry and key not in raw_packet:
                raw_packet[key] = soft_entry.get(key)
            elif key in sharp_entry and key not in raw_packet:
                raw_packet[key] = sharp_entry.get(key)

        try:
            unified.append(
                _build_unified_match(
                    display_match_name,
                    market,
                    float(sharp_odds),
                    float(soft_odds),
                    raw_packet=raw_packet,
                )
            )
        except (TypeError, ValueError) as exc:
            _emit_operator_diag(f"mac birlestirme atlandi | {display_match_name} | {exc}")
            continue

    if skipped_stale:
        _emit_scan_info(
            f"{skipped_stale} kayit zaman damgasi nedeniyle birlestirme disi birakildi"
        )
    if skipped_phase:
        _emit_scan_info(
            f"{skipped_phase} kayit evre uyumsuz (sadece mac-oncesi eslesir) birlestirme disi birakildi"
        )
    if skipped_horizon:
        _emit_scan_info(
            f"{skipped_horizon} kayit cok ileri tarihli (Nesine henuz satmiyor) bildirim disi birakildi"
        )

    return unified


def _filter_nesine_verified_soft_feed(
    soft_feed: dict[str, dict[str, str | float]],
) -> dict[str, dict[str, str | float]]:
    verified: dict[str, dict[str, str | float]] = {}
    skipped_non_nesine = 0
    skipped_virtual = 0

    for mac_id, entry in soft_feed.items():
        if not isinstance(entry, dict):
            continue

        source = str(entry.get("soft_source", "")).strip().lower()
        if source != _NESINE_SOFT_SOURCE:
            skipped_non_nesine += 1
            continue

        match_name = entry.get("match_name")
        if not isinstance(match_name, str) or not match_name.strip():
            continue

        league_name = _extract_league_name(entry)
        if is_virtual_match_text(match_name, league_name, str(mac_id)):
            skipped_virtual += 1
            continue

        verified[str(mac_id)] = entry

    if skipped_non_nesine:
        _emit_scan_info(
            f"Misli-only {skipped_non_nesine} kayit birlestirme disi birakildi "
            f"(Nesine+Sharp dogrulamasi zorunlu)"
        )
    if skipped_virtual:
        _emit_scan_info(f"E-Futbol/sanal {skipped_virtual} Nesine kaydi elendi")

    return verified


def _sharp_entry_identity(entry: dict[str, str | float]) -> tuple[str, str]:
    name = _canonicalize_team_name(str(entry.get("match_name", "")))
    return (name, str(entry.get("market", "")).strip().upper())


def _merge_sharp_sources(
    primary: dict[str, dict[str, str | float]],
    secondary: dict[str, dict[str, str | float]],
    *,
    label: str,
) -> dict[str, dict[str, str | float]]:
    """Borsa kayitlarini Odds API kapsaminin USTUNE degil, YANINA ekler.

    Ayni mac+pazar iki kaynakta da varsa Pinnacle tabanli kayit korunur; borsa
    yalnizca kotanin yetismedigi maclari doldurur. Boylece kapsam buyur, mevcut
    referans kalitesi degismez.
    """
    if not secondary:
        return primary

    known = {_sharp_entry_identity(entry) for entry in primary.values() if isinstance(entry, dict)}
    merged = dict(primary)
    added = 0
    for key, entry in secondary.items():
        if not isinstance(entry, dict):
            continue
        if _sharp_entry_identity(entry) in known:
            continue
        merged[f"{label}:{key}"] = entry
        added += 1

    if added:
        _emit_scan_info(f"{label} borsasindan {added} ek keskin kayit (0 API kredisi)")
    return merged


def check_live_feed_health() -> dict[str, Any]:
    odds_health = check_odds_api_health()
    return {
        "odds_api": odds_health,
        "sharp_last_scan": get_last_sharp_scan_diag(),
    }


def _emit_sharp_empty_diag() -> None:
    diag = get_last_sharp_scan_diag()
    kind = str(diag.get("kind", "unknown"))
    detail = str(diag.get("detail", ""))
    remaining = diag.get("quota_remaining")
    used = diag.get("quota_used")

    if kind == "empty":
        print(
            f"[SQE-V1] Teşhis: Gateway | keskin feed bos | neden={detail or 'lig takvimi bos'} | "
            f"kalan={remaining or '?'} | kullanilan={used or '?'}",
            file=sys.stderr,
        )
        return

    if kind in {"auth", "quota", "timeout", "network", "upstream", "http"}:
        _emit_operator_diag(
            f"keskin canli borsa verisi alinamadi | {kind} | {detail} | kalan={remaining or '?'}"
        )
        return

    _emit_operator_diag(f"keskin canli borsa verisi bos veya erisilemedi | {detail or kind}")


def set_dry_run_mode(enabled: bool) -> None:
    global _DRY_RUN_MODE
    _DRY_RUN_MODE = bool(enabled)
    if _DRY_RUN_MODE:
        print("[SQE-V1] Dry-Run modu aktif | mock feed kullanilacak")


def get_dry_run_live_data() -> list:
    from scrapers.dry_run_feed import get_dry_run_unified_matches

    matches = get_dry_run_unified_matches()
    _emit_scan_info(f"{len(matches)} kayit dry-run mock feed yuklendi")
    return matches


def get_unified_live_data() -> list:
    if _DRY_RUN_MODE:
        return get_dry_run_live_data()

    try:
        soft_feed = get_yasal_live_odds()
        sharp_feed = get_sharp_live_odds()
    except Exception as exc:
        _emit_operator_diag(f"canli feed okuma hatasi | {exc}")
        return []

    if not isinstance(soft_feed, dict):
        soft_feed = {}
    if not isinstance(sharp_feed, dict):
        sharp_feed = {}

    # Ikinci keskin kaynak: Matchbook borsasi (kimlik bilgisi ve kota istemez).
    # Hata durumunda mevcut akis aynen devam eder.
    try:
        sharp_feed = _merge_sharp_sources(
            sharp_feed, get_matchbook_sharp_odds(), label="matchbook"
        )
    except Exception as exc:
        _emit_operator_diag(f"matchbook kaynagi atlandi | {exc}")

    if not soft_feed and not sharp_feed:
        _emit_operator_diag("canli kaynaklardan mac verisi alinamadi")
        return []

    if not soft_feed:
        _emit_operator_diag("yasal canli bulten bos veya erisilemedi")
        return []

    if not sharp_feed:
        _emit_sharp_empty_diag()
        return []

    nesine_soft_feed = _filter_nesine_verified_soft_feed(soft_feed)
    if not nesine_soft_feed:
        _emit_operator_diag(
            "Nesine canli bulteninde sharp ile eslestirilecek gercek mac bulunamadi"
        )
        return []

    batch_epoch = time.time()

    try:
        unified = _merge_live_feeds(
            nesine_soft_feed,
            sharp_feed,
            batch_epoch=batch_epoch,
        )
    except Exception as exc:
        _emit_operator_diag(f"canli feed birlestirme hatasi | {exc}")
        return []

    if unified:
        _emit_scan_info(
            f"{len(unified)} kayit Nesine+Sharp es zamanli dogrulandi"
        )
    elif not unified:
        _emit_scan_info("Şu an her iki hatta ortak eşleşen maç yok, bekleniyor...")
        _emit_scan_info(
            f"Nesine havuzunda {len(nesine_soft_feed)} soft kayit var; "
            f"keskin borsa eslesmesi bekleniyor"
        )

    return unified


def get_settlement_feed(
    extra_sport_keys: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    try:
        return fetch_settlement_results(extra_sport_keys=extra_sport_keys)
    except Exception as exc:
        raise RuntimeError(f"settlement feed unavailable | {exc}") from exc

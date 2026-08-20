"""Steam (gecikmeli fiyat) avcisi: keskin piyasa kosarken Nesine yerinde mi?

Statik EV kapisi tek bir ana bakar: "su an Nesine orani sharp orandan yeterince
yuksek mi?". Bu, fiyatin NASIL hareket ettigini gormez. Oysa en degerli an,
sharp fiyatin belirgin sekilde dustugu (akilli para bir tarafa yuklendigi) ama
yumusak kitapcinin hala eski fiyati gosterdigi andir: piyasa gorusunu
degistirmistir, Nesine daha yetismemistir.

Bu modul, tarama motorunun zaten diske yazdigi anlik goruntuleri
(`database/feed_snapshots/*_live.json`) okur ve ayni mac+pazar icin sharp
fiyatin zaman icindeki hareketini cikarir. Yeni istek atmaz, dolayisiyla API
kredisi HARCAMAZ.

Uretilen sinyal her zaman izleme (no-play) niteligindedir: kupon acmaz, tutar
onermez. Amaci, CLV defterine bagimsiz etiketle yazilip statik EV sinyalinden
AYRI olculmesidir -- yontemin degeri kanitlanana kadar para riski alinmaz.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.price_reference import max_tier_for_reference
from core.time_utils import parse_utc

__all__ = (
    "STEAM_TIER",
    "SteamSignal",
    "build_history",
    "build_steam_message",
    "detect_steam",
    "filter_uncooled",
    "mark_notified",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SNAPSHOT_DIR = _PROJECT_ROOT / "database" / "feed_snapshots"
_STATE_PATH = _PROJECT_ROOT / "database" / "steam_state.json"

# Sinyal katmani: olcum defterinde statik EV sinyallerinden ayrilir.
STEAM_TIER = "STEAM"

# Referans fiyat en fazla bu kadar geriye gider; daha eskisi "hareket" degil,
# baska bir piyasa durumudur.
LOOKBACK_SECONDS = 6.0 * 3600.0
# Sharp oran en az bu kadar dusmus olmali (piyasa o tarafa yuklendi).
MIN_SHARP_DROP = 0.03
# Yumusak oran bu esikten fazla oynadiysa Nesine hareketi zaten takip etmis.
MAX_SOFT_DRIFT = 0.01
# Ayni mac+pazar icin tekrar bildirim araligi.
COOLDOWN_SECONDS = 4.0 * 3600.0
# Kickoff'a bu kadardan az kaldiysa bildirim gitmez (oynayacak vakit yok).
MIN_SECONDS_TO_KICKOFF = 15.0 * 60.0
# Gecmis taranirken okunacak en fazla anlik goruntu dosyasi.
_HISTORY_FILE_LIMIT = 80

_OPERATOR_DIAG = "Donanim Erisilemiyor: Steam Avcisi Hatasi"


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _normalize_name(value: Any) -> str:
    return " ".join(str(value).strip().casefold().split())


def _observation_key(record: dict[str, Any]) -> tuple[str, str]:
    """Mac+pazar kimligi: event_id varsa o, yoksa normalize mac adi."""
    event_id = str(record.get("event_id", "")).strip()
    name = event_id or _normalize_name(record.get("match_name", ""))
    return (name, str(record.get("market", "")).strip().upper())


def _observed_at(record: dict[str, Any], fallback: float) -> float:
    for key in ("sharp_observed_at", "observed_at"):
        raw = record.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > 0.0:
            return value
    return float(fallback or 0.0)


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number > 1.0 else None


def build_history(
    *,
    now: float | None = None,
    lookback_seconds: float = LOOKBACK_SECONDS,
    snapshot_dir: Path | None = None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Anlik goruntulerden mac+pazar bazinda fiyat gecmisi kurar."""
    reference_now = time.time() if now is None else float(now)
    directory = snapshot_dir or _SNAPSHOT_DIR
    if not directory.is_dir():
        return {}

    history: dict[tuple[str, str], list[dict[str, Any]]] = {}
    paths = sorted(directory.glob("*_live.json"))[-_HISTORY_FILE_LIMIT:]
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        saved_at = float(payload.get("saved_at", 0.0) or 0.0)
        for record in payload.get("matches", []):
            if not isinstance(record, dict):
                continue
            sharp = _positive_float(record.get("sharp_odds"))
            soft = _positive_float(record.get("soft_odds"))
            if sharp is None or soft is None:
                continue
            observed_at = _observed_at(record, saved_at)
            if observed_at <= 0.0 or reference_now - observed_at > lookback_seconds:
                continue
            history.setdefault(_observation_key(record), []).append(
                {"sharp_odds": sharp, "soft_odds": soft, "observed_at": observed_at}
            )

    for observations in history.values():
        observations.sort(key=lambda item: item["observed_at"])
    return history


@dataclass(frozen=True)
class SteamSignal:
    """Bir mac+pazar icin gecikmeli fiyat bulgusu (her zaman izleme)."""

    match_name: str
    market: str
    league_name: str
    sport_key: str
    event_id: str
    commence_time: str
    consensus_source: str
    consensus_books: int
    onceki_sharp: float
    sharp_odds: float
    soft_odds: float
    sharp_drop: float
    soft_drift: float
    gozlem_araligi_dk: float

    @property
    def key(self) -> tuple[str, str]:
        return (
            self.event_id or _normalize_name(self.match_name),
            self.market.strip().upper(),
        )


def _seconds_to_kickoff(commence_time: str, now: float) -> float | None:
    kickoff = parse_utc(str(commence_time))
    if kickoff is None:
        return None
    return kickoff.timestamp() - now


def _reference_observation(
    observations: Iterable[dict[str, Any]],
    *,
    now: float,
    lookback_seconds: float,
) -> dict[str, Any] | None:
    """Pencere icindeki EN ESKI gozlem: hareketin baslangic fiyati."""
    oldest: dict[str, Any] | None = None
    for observation in observations:
        age = now - float(observation["observed_at"])
        if age < 0.0 or age > lookback_seconds:
            continue
        if oldest is None or observation["observed_at"] < oldest["observed_at"]:
            oldest = observation
    return oldest


def detect_steam(
    matches: list[dict[str, Any]],
    *,
    history: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    now: float | None = None,
    min_sharp_drop: float = MIN_SHARP_DROP,
    max_soft_drift: float = MAX_SOFT_DRIFT,
    lookback_seconds: float = LOOKBACK_SECONDS,
) -> list[SteamSignal]:
    """Sharp fiyat kosarken yumusak fiyati yerinde kalan maclari bulur."""
    reference_now = time.time() if now is None else float(now)
    price_history = (
        history
        if history is not None
        else build_history(now=reference_now, lookback_seconds=lookback_seconds)
    )

    signals: list[SteamSignal] = []
    for match in matches:
        if not isinstance(match, dict):
            continue

        sharp_now = _positive_float(match.get("sharp_odds"))
        soft_now = _positive_float(match.get("soft_odds"))
        if sharp_now is None or soft_now is None:
            continue

        # Referans keskin degilse (yumusak piyasa ortalamasi) hareketin anlami yok.
        if max_tier_for_reference(str(match.get("consensus_source", ""))) is None:
            continue

        lead_seconds = _seconds_to_kickoff(str(match.get("commence_time", "")), reference_now)
        if lead_seconds is not None and lead_seconds < MIN_SECONDS_TO_KICKOFF:
            continue

        key = _observation_key(match)
        reference = _reference_observation(
            price_history.get(key, []),
            now=reference_now,
            lookback_seconds=lookback_seconds,
        )
        if reference is None:
            continue

        previous_sharp = float(reference["sharp_odds"])
        previous_soft = float(reference["soft_odds"])
        sharp_drop = (previous_sharp - sharp_now) / previous_sharp
        soft_drift = abs(previous_soft - soft_now) / previous_soft
        if sharp_drop < float(min_sharp_drop) or soft_drift > float(max_soft_drift):
            continue

        signals.append(
            SteamSignal(
                match_name=str(match.get("match_name", "")),
                market=str(match.get("market", "")).strip().upper(),
                league_name=str(match.get("league_name", "")),
                sport_key=str(match.get("sport_key", "")),
                event_id=str(match.get("event_id", "")).strip(),
                commence_time=str(match.get("commence_time", "")),
                consensus_source=str(match.get("consensus_source", "")),
                consensus_books=int(match.get("consensus_books", 0) or 0),
                onceki_sharp=round(previous_sharp, 3),
                sharp_odds=round(sharp_now, 3),
                soft_odds=round(soft_now, 3),
                sharp_drop=round(sharp_drop, 5),
                soft_drift=round(soft_drift, 5),
                gozlem_araligi_dk=round(
                    (reference_now - float(reference["observed_at"])) / 60.0, 1
                ),
            )
        )

    signals.sort(key=lambda item: item.sharp_drop, reverse=True)
    return signals


# --- Tekrar bildirim kilidi ---------------------------------------------------


def _load_state() -> dict[str, float]:
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    state: dict[str, float] = {}
    for key, value in payload.items():
        try:
            state[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return state


def _save_state(state: dict[str, float]) -> bool:
    temporary = _STATE_PATH.with_suffix(".json.tmp")
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(_STATE_PATH)
        return True
    except OSError as exc:
        _emit_operator_diag(f"durum yazilamadi | {exc}")
        return False


def _state_key(signal: SteamSignal) -> str:
    identity, market = signal.key
    return f"{identity}|{market}"


def filter_uncooled(
    signals: list[SteamSignal],
    *,
    now: float | None = None,
    cooldown_seconds: float = COOLDOWN_SECONDS,
) -> list[SteamSignal]:
    """Son `cooldown_seconds` icinde bildirilmis mac+pazarlari eler."""
    reference_now = time.time() if now is None else float(now)
    state = _load_state()
    return [
        signal
        for signal in signals
        if reference_now - state.get(_state_key(signal), 0.0) >= float(cooldown_seconds)
    ]


def mark_notified(signal: SteamSignal, *, now: float | None = None) -> bool:
    """Bildirilen sinyali kilit defterine yazar (tekrar bildirimi onler)."""
    reference_now = time.time() if now is None else float(now)
    state = _load_state()
    state[_state_key(signal)] = reference_now
    horizon = reference_now - (COOLDOWN_SECONDS * 6.0)
    pruned = {key: value for key, value in state.items() if value >= horizon}
    return _save_state(pruned)


def build_steam_message(signal: SteamSignal) -> str:
    """Telegram metni. 'Oyna' dugmesi yok: bu bir izleme bildirimidir."""
    return (
        "🌀 STEAM (fiyat hareketi) | IZLE\n"
        f"Mac: {signal.match_name}\n"
        f"Pazar: {signal.market}\n"
        f"Lig: {signal.league_name or 'Bilinmiyor'}\n"
        f"Keskin oran: {signal.onceki_sharp:.2f} -> {signal.sharp_odds:.2f} "
        f"(%{signal.sharp_drop * 100.0:.1f} dustu, {signal.gozlem_araligi_dk:.0f} dk)\n"
        f"Nesine: {signal.soft_odds:.2f} (hareketi izlemedi)\n"
        f"Referans: {signal.consensus_source or 'bilinmiyor'}\n"
        "Not: Olcum sinyali — kupon acilmaz, CLV ile ayri degerlendirilir."
    )

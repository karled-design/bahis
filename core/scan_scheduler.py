from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.league_discovery import get_league_kickoffs, has_kickoff_data

__all__ = (
    "mark_league_fetched",
    "select_due_leagues",
    "should_fetch_league_now",
)

# Akilli tarama penceresi (kredi diyeti - Adim 3).
# Oran sorgusu (lig basina ~4 kredi) artik yalnizca maca yakin iki anda hak
# edilir:
#   T-4 saat -> erken deger taramasi
#   T-45 dk  -> kapanis penceresi (CLV kapanis cizgisi de burada yakalanir)
# Ana dongu yine 10 dakikada bir tiklar ama acik pencere yoksa API'ye CIKMAZ
# (0 kredi). Son sorgu zamani diske yazilir ki yeniden baslatista ayni
# pencere ikinci kez yakilmasin. Takvim verisi yoksa sistem KILITLENMEZ:
# 4 saatte bir yedek tempoya duser (gunluk kredi tavani yine de son frendir).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "database" / "scan_window_state.json"
_LOCK = threading.Lock()

_SLOT_OFFSETS = (timedelta(hours=4), timedelta(minutes=45))
_SLOT_MERGE_GAP = timedelta(minutes=20)  # bitisik pencereler tek sorguda birlesir
_SLOT_EXPIRE_AFTER_KICKOFF = timedelta(minutes=30)  # mactan sonra pencere gecersiz
_FALLBACK_INTERVAL = timedelta(hours=4)  # takvim bilinmiyorsa yedek tempo
_MAX_TRACKED_LEAGUES = 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_moment(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {"leagues": {}}
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"leagues": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("leagues"), dict):
        return {"leagues": {}}
    return payload


def _save_state(state: dict[str, Any]) -> None:
    # Atomik yazim: yarim dosya okunmasin (defter/kesif ile ayni desen).
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _STATE_PATH.with_name(_STATE_PATH.name + ".tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, _STATE_PATH)
    except OSError as exc:
        print(f"[SQE-V1] Tarama pencere durumu yazilamadi | {exc}", file=sys.stderr)


def _get_last_fetch(sport_key: str) -> datetime | None:
    with _LOCK:
        entry = _load_state().get("leagues", {}).get(sport_key)
    if not isinstance(entry, dict):
        return None
    return _parse_moment(entry.get("last_fetch_at"))


def mark_league_fetched(sport_key: str, *, now: datetime | None = None) -> None:
    """Basarili (faturalanmis) oran sorgusundan sonra cagrilir."""
    moment = now or _now_utc()
    with _LOCK:
        state = _load_state()
        leagues = state.get("leagues")
        if not isinstance(leagues, dict):
            leagues = {}
        leagues[str(sport_key)] = {
            "last_fetch_at": moment.isoformat().replace("+00:00", "Z")
        }
        if len(leagues) > _MAX_TRACKED_LEAGUES:
            # En eski kayitlari at (dosya sisimesin)
            ordered = sorted(
                leagues.items(),
                key=lambda item: str(item[1].get("last_fetch_at", "")) if isinstance(item[1], dict) else "",
            )
            leagues = dict(ordered[-_MAX_TRACKED_LEAGUES:])
        state["leagues"] = leagues
        _save_state(state)


def _league_slots(sport_key: str, now: datetime) -> list[tuple[datetime, datetime]]:
    """Ligin (pencere_baslangici, pencere_bitisi) listesi — birlesik ve sirali."""
    raw_slots: list[tuple[datetime, datetime]] = []
    for kickoff in get_league_kickoffs(sport_key):
        expire = kickoff + _SLOT_EXPIRE_AFTER_KICKOFF
        if expire <= now:
            continue
        for offset in _SLOT_OFFSETS:
            raw_slots.append((kickoff - offset, expire))
    raw_slots.sort(key=lambda item: item[0])

    merged: list[tuple[datetime, datetime]] = []
    for slot_time, expire in raw_slots:
        if merged and (slot_time - merged[-1][0]) < _SLOT_MERGE_GAP:
            # Yakin iki pencere: gec olani tut (kapanisa daha yakin veri),
            # bitis olarak genis olani koru.
            merged[-1] = (slot_time, max(expire, merged[-1][1]))
            continue
        merged.append((slot_time, expire))
    return merged


def should_fetch_league_now(
    sport_key: str,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Bu lig icin SU AN oran sorgusu hak edilmis mi?

    Donen metin teshis icindir: pencere aciksa hangisi, degilse siradaki
    pencere ne zaman.
    """
    moment = now or _now_utc()
    last_fetch = _get_last_fetch(sport_key)

    if not has_kickoff_data(sport_key):
        # Takvim bilinmiyor: 4 saatlik yedek tempo (kilitlenme olmasin).
        if last_fetch is None or (moment - last_fetch) >= _FALLBACK_INTERVAL:
            return True, "takvim bilinmiyor - yedek tempo"
        return False, "takvim bilinmiyor - yedek tempo bekliyor"

    slots = _league_slots(sport_key, moment)
    next_slot: datetime | None = None
    for slot_time, expire in slots:
        if slot_time <= moment <= expire:
            if last_fetch is None or last_fetch < slot_time:
                local = slot_time.astimezone().strftime("%H:%M")
                return True, f"pencere acik (baslangic {local})"
        elif slot_time > moment and next_slot is None:
            next_slot = slot_time

    if next_slot is not None:
        local = next_slot.astimezone().strftime("%H:%M")
        return False, f"siradaki pencere {local}"
    return False, "yakin pencerede mac yok"


def select_due_leagues(
    sport_keys: list[str],
    *,
    now: datetime | None = None,
) -> tuple[list[str], str]:
    """Penceresi acik ligleri secer; bos ise tek satirlik neden dondurur."""
    moment = now or _now_utc()
    due: list[str] = []
    reasons: list[str] = []
    for sport_key in sport_keys:
        is_due, reason = should_fetch_league_now(sport_key, now=moment)
        if is_due:
            due.append(sport_key)
        else:
            reasons.append(f"{sport_key}: {reason}")
    note = "" if due else " | ".join(reasons[:4])
    return due, note

"""Telegram dinleyici sahiplik kilidi: ayni bot token'ini iki motor dinleyemez.

Telegram bir bota tek `getUpdates` dinleyicisi izin verir; iki makinede motor
acik kalirsa ikisi de HTTP 409 alir ve butonlar/komutlar islenmez. Makine ici
PID dosyasi bunu goremez, cunku diger motor baska bir bilgisayardadir.

Cozum: bot profilinin "kisa aciklama" alani token basina paylasilan tek bir
kayit defteri gibi kullanilir. Motor kalkarken buraya kimlik + zaman damgasi
yazar (devralma). Cakisma yasayan motor defteri okur; kayit kendisinden yeniyse
dinlemeyi birakip operatore secenekleri bildirir. Para/kupon yolu ile ilgisi
yoktur.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from config.settings import TELEGRAM_TOKEN

__all__ = (
    "OwnerClaim",
    "build_instance_id",
    "publish_claim",
    "read_claim",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Telegram Sahiplik Kaydi Hatasi"
_GET_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMyShortDescription"
_SET_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyShortDescription"
_REQUEST_TIMEOUT_SECONDS = 10.0
_CLAIM_PREFIX = "SQE motor:"
_MAX_SHORT_DESCRIPTION_CHARS = 120


@dataclass(frozen=True)
class OwnerClaim:
    instance_id: str
    claimed_at: float


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def build_instance_id() -> str:
    """Motoru insan tarafindan taninabilir kilan kisa kimlik."""
    label = os.getenv("TELEGRAM_MOTOR_ADI", "").strip()
    host = label or socket.gethostname()
    return f"{host[:40]}#{os.getpid()}"


def _encode(claim: OwnerClaim) -> str:
    payload = json.dumps(
        {"id": claim.instance_id, "ts": round(claim.claimed_at, 3)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{_CLAIM_PREFIX}{payload}"[:_MAX_SHORT_DESCRIPTION_CHARS]


def _decode(raw: str) -> OwnerClaim | None:
    if not raw.startswith(_CLAIM_PREFIX):
        return None
    try:
        payload = json.loads(raw[len(_CLAIM_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    instance_id = payload.get("id")
    claimed_at = payload.get("ts")
    if not isinstance(instance_id, str) or not isinstance(claimed_at, (int, float)):
        return None
    return OwnerClaim(instance_id=instance_id, claimed_at=float(claimed_at))


def _request(url: str, payload: dict[str, object] | None = None) -> dict[str, object] | None:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        _emit_operator_diag(str(exc))
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        return None
    return parsed


def publish_claim(instance_id: str, *, now: float | None = None) -> OwnerClaim | None:
    """Bu motoru aktif dinleyici olarak isaretler (devralma)."""
    claim = OwnerClaim(instance_id=instance_id, claimed_at=now if now is not None else time.time())
    if _request(_SET_URL, {"short_description": _encode(claim)}) is None:
        return None
    return claim


def read_claim() -> OwnerClaim | None:
    """Token uzerindeki guncel sahiplik kaydini okur; kayit yoksa None."""
    parsed = _request(_GET_URL)
    if parsed is None:
        return None
    result = parsed.get("result")
    if not isinstance(result, dict):
        return None
    raw = result.get("short_description")
    if not isinstance(raw, str):
        return None
    return _decode(raw)

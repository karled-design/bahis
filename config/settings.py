# SQE-V1 Global Configuration Settings
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

__all__ = (
    "ConfigurationError",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ODDS_API_KEY",
    "TOTAL_KASA",
    "RISK_PER_TRADE",
    "MIN_VALUE_THRESHOLD",
    "SOFT_MARKET_LAG_TIMEOUT",
    "PILOT_MODE",
    "VIRTUAL_WALLET",
    "EXECUTION_BOOK",
    "MAX_EV_THRESHOLD",
    "MIN_CONSENSUS_BOOKMAKERS",
    "PILOT_MIN_VALUE_THRESHOLD",
    "WATCH_EV_THRESHOLD",
    "HIGH_EV_THRESHOLD",
    "SCAN_INTERVAL_SECONDS",
    "COOLDOWN_ODDS_CHANGE_BYPASS_PCT",
    "PAPER_SLIPPAGE_PCT",
    "CONTEXT_FUSION_MODE",
    "API_FOOTBALL_KEY",
    "CONTEXT_ENRICH_MODE",
    "SQE_DB_PATH",
    "resolve_sqe_db_path",
    "HERO_MODE",
)


class ConfigurationError(RuntimeError):
    """Invalid or missing SQE-V1 configuration."""


TELEGRAM_TOKEN: Final[str] = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID: Final[str] = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ODDS_API_KEY: Final[str] = os.getenv("ODDS_API_KEY", "").strip()

TOTAL_KASA: Final[float] = 20000.0  # Başlangıç Operatör Sermayesi (TL)
RISK_PER_TRADE: Final[float] = 0.02  # Maç başı taban kasa riski (varsayılan %2). Canlı değer aktif bildirim profilinden gelir. Tutar artık YARIM-KELLY ile hesaplanır (core/clv_engine.py); bu oran maç başı üst sınırı verir (en fazla 2×). Oran bilinmezse eski formüle düşülür.
MIN_VALUE_THRESHOLD: Final[float] = 0.03  # Minimum beklenen değer eşiği ($EV$ >= %3)
PILOT_MIN_VALUE_THRESHOLD: Final[float] = 0.025  # Pilot modda ACTION eşiği (%2.5)
WATCH_EV_THRESHOLD: Final[float] = 0.015  # IZLE katmani alt siniri (%1.5)
HIGH_EV_THRESHOLD: Final[float] = 0.05  # YUKSEK katmani alt siniri (%5)
MAX_EV_THRESHOLD: Final[float] = 0.15  # Absurt EV ust siniri (>%15 = muhtemel eslesme hatasi)
MIN_CONSENSUS_BOOKMAKERS: Final[int] = 2  # Referans oran icin en az sharp bookmaker sayisi
PILOT_SCAN_INTERVAL_SECONDS: Final[int] = 180  # Pilot tarama araligi (sn)
LIVE_SCAN_INTERVAL_SECONDS: Final[int] = 600  # Canli tarama araligi (sn)
COOLDOWN_ODDS_CHANGE_BYPASS_PCT: Final[float] = 0.05  # Oran %5+ degisince cooldown bypass
PAPER_SLIPPAGE_PCT: Final[float] = 0.015  # Pilot paper trade: soft oran -%1.5 slippage
# Context fusion: filter = ACTION/HIGH yalnizca baglam uyumluysa (baglam yoksa EV-only)
CONTEXT_FUSION_MODE: Final[str] = os.getenv("CONTEXT_FUSION_MODE", "filter").strip().casefold() or "filter"
API_FOOTBALL_KEY: Final[str] = os.getenv("API_FOOTBALL_KEY", "").strip()
# Context enrich: auto = API key varsa acik; dry-run/main.py ayri kontrol eder
CONTEXT_ENRICH_MODE: Final[str] = os.getenv("CONTEXT_ENRICH_MODE", "auto").strip().casefold() or "auto"

# Yasal Altyapı Hantal Pazar Ayarları (Nesine / Misli Entegrasyon Referansı)
SOFT_MARKET_LAG_TIMEOUT: Final[int] = 5  # Maksimum istek zaman aşımı (Saniye)

# Canli mod: gercek oranlar, operator butcesi panelden girilir
PILOT_MODE: Final[bool] = False
VIRTUAL_WALLET: Final[bool] = False
EXECUTION_BOOK: Final[str] = "nesine"
# HERO: kazanma ihtimali + form + piyasa uyumu (EV avcisi kapali); varsayilan kapali
HERO_MODE: Final[bool] = os.getenv("HERO_MODE", "false").strip().casefold() in {
    "1",
    "true",
    "yes",
    "on",
}

_DEFAULT_LIVE_DB = "database/sqe_storage.db"
_DEFAULT_PILOT_DB = "database/sqe_pilot.db"


def resolve_sqe_db_path(
    *,
    project_root: Path | None = None,
    pilot_mode: bool | None = None,
    env_value: str | None = None,
) -> Path:
    """SQLite path: SQE_DB_PATH env > pilot default > live default."""
    root = project_root or _PROJECT_ROOT
    raw_env = env_value if env_value is not None else os.getenv("SQE_DB_PATH", "").strip()
    if raw_env:
        path = Path(raw_env)
        if not path.is_absolute():
            path = root / path
        return path

    use_pilot = PILOT_MODE if pilot_mode is None else pilot_mode
    relative = _DEFAULT_PILOT_DB if use_pilot else _DEFAULT_LIVE_DB
    return root / relative


SQE_DB_PATH: Final[Path] = resolve_sqe_db_path()

SCAN_INTERVAL_SECONDS: Final[int] = (
    PILOT_SCAN_INTERVAL_SECONDS if PILOT_MODE else LIVE_SCAN_INTERVAL_SECONDS
)


def _require_non_empty_str(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{name}: string required, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ConfigurationError(f"{name}: must not be empty")
    return stripped


def _require_positive_float(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name}: numeric required, got {type(value).__name__}")
    numeric = float(value)
    if numeric <= 0.0:
        raise ConfigurationError(f"{name}: must be > 0, got {numeric}")
    return numeric


def _require_open_unit_interval(name: str, value: object) -> float:
    numeric = _require_positive_float(name, value)
    if numeric >= 1.0:
        raise ConfigurationError(f"{name}: must be < 1, got {numeric}")
    return numeric


def _require_positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name}: int required, got {type(value).__name__}")
    if value <= 0:
        raise ConfigurationError(f"{name}: must be > 0, got {value}")
    return value


def _validate_configuration() -> None:
    token = _require_non_empty_str("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
    if ":" not in token:
        raise ConfigurationError("TELEGRAM_TOKEN: expected bot token format '<id>:<secret>'")

    _require_non_empty_str("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    _require_non_empty_str("ODDS_API_KEY", ODDS_API_KEY)
    _require_positive_float("TOTAL_KASA", TOTAL_KASA)
    _require_open_unit_interval("RISK_PER_TRADE", RISK_PER_TRADE)
    _require_open_unit_interval("MIN_VALUE_THRESHOLD", MIN_VALUE_THRESHOLD)
    pilot_min = _require_open_unit_interval("PILOT_MIN_VALUE_THRESHOLD", PILOT_MIN_VALUE_THRESHOLD)
    watch_ev = _require_open_unit_interval("WATCH_EV_THRESHOLD", WATCH_EV_THRESHOLD)
    if watch_ev >= pilot_min:
        raise ConfigurationError(
            f"WATCH_EV_THRESHOLD: must be < PILOT_MIN_VALUE_THRESHOLD ({pilot_min}), got {watch_ev}"
        )
    high_ev = _require_positive_float("HIGH_EV_THRESHOLD", HIGH_EV_THRESHOLD)
    if high_ev <= float(MIN_VALUE_THRESHOLD):
        raise ConfigurationError(
            f"HIGH_EV_THRESHOLD: must be > MIN_VALUE_THRESHOLD ({MIN_VALUE_THRESHOLD}), got {high_ev}"
        )
    max_ev = _require_positive_float("MAX_EV_THRESHOLD", MAX_EV_THRESHOLD)
    if max_ev <= float(MIN_VALUE_THRESHOLD):
        raise ConfigurationError(
            f"MAX_EV_THRESHOLD: must be > MIN_VALUE_THRESHOLD ({MIN_VALUE_THRESHOLD}), got {max_ev}"
        )
    _require_positive_int("MIN_CONSENSUS_BOOKMAKERS", MIN_CONSENSUS_BOOKMAKERS)
    _require_positive_int("SOFT_MARKET_LAG_TIMEOUT", SOFT_MARKET_LAG_TIMEOUT)
    _require_positive_int("PILOT_SCAN_INTERVAL_SECONDS", PILOT_SCAN_INTERVAL_SECONDS)
    _require_positive_int("LIVE_SCAN_INTERVAL_SECONDS", LIVE_SCAN_INTERVAL_SECONDS)
    bypass = _require_positive_float("COOLDOWN_ODDS_CHANGE_BYPASS_PCT", COOLDOWN_ODDS_CHANGE_BYPASS_PCT)
    if bypass >= 1.0:
        raise ConfigurationError(f"COOLDOWN_ODDS_CHANGE_BYPASS_PCT: must be < 1, got {bypass}")
    slippage = _require_positive_float("PAPER_SLIPPAGE_PCT", PAPER_SLIPPAGE_PCT)
    if slippage >= 1.0:
        raise ConfigurationError(f"PAPER_SLIPPAGE_PCT: must be < 1, got {slippage}")
    fusion_mode = _require_non_empty_str("CONTEXT_FUSION_MODE", CONTEXT_FUSION_MODE)
    if fusion_mode not in {"off", "info", "filter"}:
        raise ConfigurationError("CONTEXT_FUSION_MODE: must be 'off', 'info', or 'filter'")
    enrich_mode = _require_non_empty_str("CONTEXT_ENRICH_MODE", CONTEXT_ENRICH_MODE)
    if enrich_mode not in {"off", "on", "auto"}:
        raise ConfigurationError("CONTEXT_ENRICH_MODE: must be 'off', 'on', or 'auto'")


def _bootstrap() -> None:
    try:
        _validate_configuration()
    except ConfigurationError as exc:
        print(f"[config.settings] FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


_bootstrap()

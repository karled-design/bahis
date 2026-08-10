from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

# Canli durum dosyalarinin tam listesi: (modul, yol sabiti, gecici dosya adi).
# Buradaki her yol, yalitim aktifken gecici klasore yonlendirilir; boylece
# test takimi kosarken panelin/motorun gercek ayar ve durum dosyalari
# (scan_leagues, hero durumlari, gunlukler...) ASLA degismez.
_LIVE_PATH_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("core.api_credit_ledger", "_LEDGER_PATH", "api_credit_ledger.json"),
    ("core.daily_digest", "_STATE_PATH", "daily_digest_state.json"),
    ("core.experimental.mode", "_PERSIST_PATH", "experimental_mode.json"),
    ("core.experimental.shadow_log", "_LOG_PATH", "experimental_shadow.jsonl"),
    ("core.hero_daily_guard", "_STATE_PATH", "hero_daily_state.json"),
    ("core.hero_measurement", "_STATE_PATH", "hero_measurement.json"),
    ("core.hero_mode", "_PERSIST_PATH", "hero_mode.json"),
    ("core.hero_profile", "_PERSIST_PATH", "hero_profile.json"),
    ("core.hero_weekly_guard", "_STATE_PATH", "hero_weekly_state.json"),
    ("core.kickoff_reminder", "_STATE_PATH", "kickoff_reminder_state.json"),
    ("core.league_discovery", "_CACHE_PATH", "league_discovery.json"),
    ("core.live_alert_settings", "_PERSIST_PATH", "live_alerts.json"),
    ("core.notify_frequency_profile", "_PERSIST_PATH", "notify_frequency.json"),
    ("core.operator_risk_settings", "_PERSIST_PATH", "operator_risk.json"),
    ("core.scan_league_settings", "_PERSIST_PATH", "scan_leagues.json"),
    ("core.scan_scheduler", "_STATE_PATH", "scan_window_state.json"),
    ("core.settlement_log", "_DEFAULT_LOG_PATH", "settlement_log.jsonl"),
    ("core.shadow_consensus", "_SHADOW_PATH", "shadow_consensus.jsonl"),
)


class LiveStateIsolation:
    """Testleri canli database dosyalarindan yalitan koruma.

    start() bilinen tum durum-dosyasi yollarini tek bir gecici klasore
    cevirir; stop() yollari geri alir, gecici klasoru siler ve bellekte
    ayar tutan modullerin (scan_league_settings) canli degerlerini geri koyar.
    """

    def __init__(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._patchers: list[Any] = []
        self._saved_scan_state: tuple[int, list[str], bool] | None = None

    def start(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for module_name, attr, filename in _LIVE_PATH_TARGETS:
            module = importlib.import_module(module_name)
            patcher = mock.patch.object(module, attr, root / filename)
            patcher.start()
            self._patchers.append(patcher)

        # scan_league_settings secimi bellekte de tutar: canli secimi sakla,
        # yalitim biterken aynen geri koy (dosya + bellek ikisi de temiz kalsin).
        sls = importlib.import_module("core.scan_league_settings")
        self._saved_scan_state = (
            int(sls._STATE["leagues_per_scan"]),
            list(sls._STATE["enabled_sport_keys"]),
            bool(sls._AUTO_SEASON),
        )
        return root

    def stop(self) -> None:
        if self._saved_scan_state is not None:
            sls = importlib.import_module("core.scan_league_settings")
            per_scan, keys, auto = self._saved_scan_state
            sls._STATE["leagues_per_scan"] = per_scan
            sls._STATE["enabled_sport_keys"] = list(keys)
            sls._AUTO_SEASON = auto
            self._saved_scan_state = None
        while self._patchers:
            self._patchers.pop().stop()
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


def isolate_module(module_globals: dict[str, Any]) -> None:
    """Bir test dosyasini tek satirla yalitir.

    Dosyanin ust duzeyinde `isolate_module(globals())` cagrilir; unittest'in
    setUpModule/tearDownModule kancalari kurulur ve dosyadaki TUM testler
    kosarken canli dosya yollari gecici klasorde kalir. Dosyada zaten
    setUpModule/tearDownModule varsa onlar da calistirilmaya devam eder.
    """
    guard = LiveStateIsolation()

    existing_setup = module_globals.get("setUpModule")
    existing_teardown = module_globals.get("tearDownModule")

    def setUpModule() -> None:  # noqa: N802 - unittest kancasi
        guard.start()
        if callable(existing_setup):
            existing_setup()

    def tearDownModule() -> None:  # noqa: N802 - unittest kancasi
        try:
            if callable(existing_teardown):
                existing_teardown()
        finally:
            guard.stop()

    module_globals["setUpModule"] = setUpModule
    module_globals["tearDownModule"] = tearDownModule

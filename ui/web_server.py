from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import EXECUTION_BOOK, PILOT_MODE, VIRTUAL_WALLET
from core import clv_tracker
from core.api_credit_ledger import build_credit_panel_payload
from core.league_discovery import annotate_scan_leagues_payload
from core.experimental_features import (
    bootstrap_experimental_mode,
    build_experimental_panel_payload,
    set_experimental_mode,
    update_experimental_features,
)
from core.hero_mode import (
    bootstrap_hero_mode,
    build_hero_panel_payload,
    is_hero_mode_enabled,
    set_hero_mode_enabled,
)
from core.hero_profile import apply_hero_profile_level, bootstrap_hero_profile
from core.notify_frequency_profile import (
    apply_notify_frequency_level,
    bootstrap_notify_frequency_profile,
    build_notify_frequency_payload,
)
from core.live_alert_settings import (
    is_live_alerts_enabled,
    set_live_alerts_enabled,
)
from core.strategy_settings import (
    get_strategy_panel_payload,
    is_side_markets_effective,
    is_side_markets_enabled,
    is_side_markets_frozen,
    set_side_markets_enabled,
)
from core.scan_league_settings import (
    build_scan_leagues_payload,
    update_scan_league_settings,
)
from core.operator_risk_settings import (
    apply_risk_preset,
    bootstrap_operator_risk_settings,
    build_risk_presets_payload,
    build_slider_payload,
    detect_active_risk_preset,
    get_action_ev_threshold,
    get_context_mode,
    get_operator_risk_per_trade,
    get_operator_risk_settings,
    get_watch_ev_threshold,
    update_operator_risk_settings,
)
from database.db_manager import (
    add_authorized_ip,
    count_notifications_between,
    get_db_path,
    get_latest_bakiye,
    get_pending_kupons,
    get_performance_stats,
    get_pilot_ab_summary,
    get_recent_kupons,
    is_ip_authorized,
    is_operator_budget_configured,
    recalibrate_kasa_from_baseline,
    reset_operator_tracking,
    result_kupon,
    save_bakiye,
    set_kupon_alinan_oran,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_INDEX_PATH = _PROJECT_ROOT / "index.html"
_WEB_HOST = "0.0.0.0"
_WEB_PORT = 8765
_MAX_POST_BYTES = 1_000_000
_STATUS_LOCK = threading.Lock()
_SERVER: ThreadingHTTPServer | None = None
_SERVER_THREAD: threading.Thread | None = None

_KUPON_API_DIAG = "Donanim Erisilemiyor: Kupon Sonuclandirma API Hatasi"
_VALID_RESULTS = frozenset({"WON", "LOST"})
_KUPON_PENDING = "PENDING"
_LOCALHOST_IPS = frozenset({"127.0.0.1", "::1"})
_TRUSTED_VPN_IP_PREFIX = "100."
_IP_PROTECTED_ROUTES = frozenset({
    "/api/finance_metrics",
    "/api/update_kasa",
    "/api/risk_settings",
    "/api/risk_preset",
    "/api/experimental_features",
    "/api/experimental_mode",
    "/api/scan_leagues",
    "/api/notify_frequency",
    "/api/hero_mode",
    "/api/hero_profile",
    "/api/reset_tracking",
    "/api/recalibrate_kasa",
    "/api/result_kupon",
    "/api/play_signal",
    "/api/skip_signal",
    "/api/set_played_odds",
})

_DEFAULT_PERFORMANCE: dict[str, Any] = {
    "total_kupon": 0,
    "pending": 0,
    "won": 0,
    "lost": 0,
    "win_rate": 0.0,
    "total_staked": 0.0,
    "total_payout": 0.0,
    "net_pl": 0.0,
    "roi_percent": 0.0,
}

SISTEM_DURUMU: dict[str, Any] = {
    "total_kasa": 0.0,
    "risk_per_trade": 0.0,
    "active_match_count": 0,
    "alarm_candidate_count": 0,
    "ev_threshold": 0.03,
    "is_live": False,
    "scan_enabled": False,
    "pilot_mode": False,
    "execution_book": str(EXECUTION_BOOK),
    "matches": [],
    "pending_kupons": [],
    "recent_kupons": [],
    "performance": dict(_DEFAULT_PERFORMANCE),
    "last_scan": {},
}


def _emit_operator_diag(detail: str) -> None:
    print(f"[web_server] {detail}", file=sys.stderr)


def _is_trusted_vpn_ip(ip: str) -> bool:
    if not isinstance(ip, str) or not ip.strip():
        return False
    normalized = ip.strip().casefold()
    if normalized.startswith(_TRUSTED_VPN_IP_PREFIX):
        return True
    # Tailscale IPv6 (ULA fd7a:...)
    return normalized.startswith("fd7a:")


def _emit_kupon_api_diag(detail: str) -> None:
    print(f"{_KUPON_API_DIAG}: {detail}", file=sys.stderr)


def _load_performance_stats() -> dict[str, Any]:
    try:
        stats = get_performance_stats()
        if not isinstance(stats, dict):
            _emit_kupon_api_diag("performance stats must be a dict")
            return json.loads(json.dumps(_DEFAULT_PERFORMANCE))
        return stats
    except (TypeError, ValueError) as exc:
        _emit_kupon_api_diag(f"performance stats parse failed | {exc}")
        return json.loads(json.dumps(_DEFAULT_PERFORMANCE))
    except Exception as exc:
        _emit_kupon_api_diag(f"performance stats read failed | {exc}")
        return json.loads(json.dumps(_DEFAULT_PERFORMANCE))


def _fetch_pending_kupons() -> list[dict[str, Any]]:
    try:
        return get_pending_kupons()
    except (TypeError, ValueError) as exc:
        _emit_kupon_api_diag(f"pending kupon parse failed | {exc}")
        return []


def _fetch_recommendations() -> list[dict[str, Any]]:
    """Panel bildirim akisi: su an gecerli bahis onerileri (salt-okuma).

    telegram_worker'i fonksiyon icinde (gec/lazy) import eder; boylece
    modul tepesinde dongusel import (telegram_worker -> web_server) olusmaz.
    Herhangi bir hata durum yanitini asla bozmasin diye genis yakalanir.
    """
    try:
        from notifiers.telegram_worker import get_active_recommendations

        result = get_active_recommendations()
        return result if isinstance(result, list) else []
    except Exception as exc:  # noqa: BLE001 - panel durumu asla bu yuzden kirilmasin
        _emit_kupon_api_diag(f"recommendations read failed | {exc}")
        return []


def _load_clv_scorecard() -> dict[str, Any]:
    """CLV karnesini güvenli okur; hata olursa boş karne döner."""
    try:
        card = clv_tracker.scorecard()
        if isinstance(card, dict):
            return card
        return {"olculen": 0, "lehte": 0, "lehte_oran": 0.0, "ort_clv_pct": 0.0}
    except Exception as exc:
        _emit_kupon_api_diag(f"clv scorecard read failed | {exc}")
        return {"olculen": 0, "lehte": 0, "lehte_oran": 0.0, "ort_clv_pct": 0.0}


def _build_finance_metrics_payload() -> dict[str, Any]:
    try:
        stats = _load_performance_stats()
        roi_value = float(stats.get("roi_percent", 0.0))
        net_kar_value = float(stats.get("net_pl", 0.0))
        return {
            "status": "online",
            "roi": round(roi_value, 2),
            "net_kar": round(net_kar_value, 2),
        }
    except (TypeError, ValueError) as exc:
        _emit_kupon_api_diag(f"finance_metrics parse failed | {exc}")
        return {"status": "online", "roi": 0.0, "net_kar": 0.0}


def _count_today_notifications() -> int:
    """Turkiye gunune gore bugun gonderilen bildirim sayisi (durum seridi icin)."""
    now_tr = datetime.now(ZoneInfo("Europe/Istanbul"))
    day_start = now_tr.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = day_start.astimezone(timezone.utc).isoformat(timespec="seconds")
    end_utc = (day_start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds")
    return count_notifications_between(start_utc, end_utc)


def _build_status_payload() -> dict[str, Any]:
    budget_configured = is_operator_budget_configured()
    live_kasa = round(float(get_latest_bakiye()), 2) if budget_configured else 0.0
    with _STATUS_LOCK:
        payload = json.loads(json.dumps(SISTEM_DURUMU))
        payload["total_kasa"] = live_kasa
    payload["pending_kupons"] = _fetch_pending_kupons()
    payload["recommendations"] = _fetch_recommendations()
    payload["recent_kupons"] = get_recent_kupons(limit=30)
    payload["performance"] = _load_performance_stats()
    payload["pilot_mode"] = bool(PILOT_MODE)
    payload["virtual_wallet"] = bool(VIRTUAL_WALLET)
    _clv_card = _load_clv_scorecard()
    payload["clv_scorecard"] = _clv_card
    # Asama 4: panel "TEST / GERCEK" rozeti icin kanit durumu (tek kaynak: clv_tracker)
    payload["clv_proven"] = clv_tracker.is_scorecard_proven(_clv_card)
    payload["clv_proof_min_sample"] = clv_tracker.CLV_PROOF_MIN_SAMPLE
    payload["budget_configured"] = budget_configured
    payload["budget_required"] = not budget_configured
    payload["execution_book"] = str(EXECUTION_BOOK)
    payload["risk_settings"] = build_slider_payload()
    payload["risk_presets"] = build_risk_presets_payload()
    payload["active_risk_preset"] = detect_active_risk_preset()
    payload["risk_per_trade"] = get_operator_risk_per_trade()
    payload["ev_threshold"] = get_action_ev_threshold()
    payload["watch_ev_threshold"] = get_watch_ev_threshold()
    payload["context_mode"] = get_context_mode()
    experimental_panel = build_experimental_panel_payload()
    payload["experimental"] = experimental_panel
    payload["experimental_features"] = experimental_panel["features"]
    payload["scan_leagues"] = annotate_scan_leagues_payload(build_scan_leagues_payload())
    payload["notify_frequency"] = build_notify_frequency_payload()
    payload["hero"] = build_hero_panel_payload()
    payload["pilot_ab"] = get_pilot_ab_summary()
    payload["today_notifications"] = _count_today_notifications()
    payload["live_alerts_enabled"] = is_live_alerts_enabled()
    payload["api_credits"] = build_credit_panel_payload()
    payload["strategy"] = get_strategy_panel_payload()
    return payload


def _snapshot_status() -> dict[str, Any]:
    return _build_status_payload()


def update_sistem_durumu(
    *,
    total_kasa: float | None = None,
    risk_per_trade: float | None = None,
    active_match_count: int | None = None,
    alarm_candidate_count: int | None = None,
    ev_threshold: float | None = None,
    is_live: bool | None = None,
    scan_enabled: bool | None = None,
    matches: list[dict[str, Any]] | None = None,
    pending_kupons: list[dict[str, Any]] | None = None,
    performance: dict[str, Any] | None = None,
    last_scan: dict[str, Any] | None = None,
) -> None:
    with _STATUS_LOCK:
        if total_kasa is not None:
            SISTEM_DURUMU["total_kasa"] = float(total_kasa)
        if risk_per_trade is not None:
            SISTEM_DURUMU["risk_per_trade"] = float(risk_per_trade)
        if active_match_count is not None:
            SISTEM_DURUMU["active_match_count"] = int(active_match_count)
        if alarm_candidate_count is not None:
            SISTEM_DURUMU["alarm_candidate_count"] = int(alarm_candidate_count)
        if ev_threshold is not None:
            SISTEM_DURUMU["ev_threshold"] = float(ev_threshold)
        if is_live is not None:
            SISTEM_DURUMU["is_live"] = bool(is_live)
        if scan_enabled is not None:
            SISTEM_DURUMU["scan_enabled"] = bool(scan_enabled)
        if matches is not None:
            SISTEM_DURUMU["matches"] = matches
        if pending_kupons is not None:
            SISTEM_DURUMU["pending_kupons"] = pending_kupons
        if performance is not None:
            SISTEM_DURUMU["performance"] = performance
        if last_scan is not None:
            SISTEM_DURUMU["last_scan"] = last_scan


def _toggle_scan_enabled() -> bool:
    with _STATUS_LOCK:
        current = bool(SISTEM_DURUMU.get("scan_enabled", False))
        enabled = not current
        SISTEM_DURUMU["scan_enabled"] = enabled
        SISTEM_DURUMU["is_live"] = enabled
        return enabled


class _OperatorPanelHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _client_ip(self) -> str:
        return str(self.client_address[0])

    def _is_localhost_client(self) -> bool:
        return self._client_ip() in _LOCALHOST_IPS

    def _is_trusted_client(self) -> bool:
        client_ip = self._client_ip()
        return client_ip in _LOCALHOST_IPS or _is_trusted_vpn_ip(client_ip)

    def _apply_cors_if_needed(self) -> None:
        origin = self.headers.get("Origin")
        if not isinstance(origin, str) or not origin:
            return
        if origin.startswith("http://100.") or origin.startswith("https://100."):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            return
        if origin.casefold().startswith("http://[fd7a:") or origin.casefold().startswith("https://[fd7a:"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _enforce_ip_policy(self, route: str) -> bool:
        if route not in _IP_PROTECTED_ROUTES:
            return True
        client_ip = self._client_ip()
        if client_ip in _LOCALHOST_IPS or _is_trusted_vpn_ip(client_ip) or is_ip_authorized(client_ip):
            return True
        _emit_operator_diag(f"Teşhis: Yetkisiz Terminal Erişimi - IP: {client_ip}")
        self._send_json(
            403,
            {
                "ok": False,
                "error": "forbidden",
                "detail": (
                    "Bu islem icin IP yetkisi gerekir. Ayni bilgisayardan "
                    "http://127.0.0.1:8765 acin veya yetkili IP ekleyin."
                ),
            },
        )
        return False

    def _send_bytes(self, status_code: int, content_type: str, payload: bytes) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self._apply_cors_if_needed()
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            _emit_kupon_api_diag(f"json encode failed | {exc}")
            body = b'{"ok":false}'
            status_code = 500
        self._send_bytes(status_code, "application/json; charset=utf-8", body)

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length_header = self.headers.get("Content-Length", "0")
            length = int(length_header)
            if length < 0 or length > _MAX_POST_BYTES:
                _emit_kupon_api_diag(f"invalid Content-Length | {length_header}")
                return None
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(parsed, dict):
                _emit_kupon_api_diag("json body must be an object")
                return None
            return parsed
        except json.JSONDecodeError as exc:
            _emit_kupon_api_diag(f"json decode failed | {exc}")
            return None
        except (ValueError, OSError, UnicodeError) as exc:
            _emit_kupon_api_diag(f"body read failed | {exc}")
            return None

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._apply_cors_if_needed()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if not self._enforce_ip_policy(route):
            return

        if route in {"/", "/index.html"}:
            try:
                if not _INDEX_PATH.is_file():
                    self._send_bytes(404, "text/plain; charset=utf-8", b"index.html not found")
                    return
                content = _INDEX_PATH.read_bytes()
                self._send_bytes(200, "text/html; charset=utf-8", content)
            except OSError as exc:
                _emit_operator_diag(f"index read failed | {exc}")
                self._send_bytes(500, "text/plain; charset=utf-8", b"index read error")
            return

        if route == "/api/status":
            try:
                status_payload = _build_status_payload()
                body = json.dumps(status_payload, ensure_ascii=False).encode("utf-8")
                self._send_bytes(200, "application/json; charset=utf-8", body)
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"status serialize failed | {exc}")
                with _STATUS_LOCK:
                    fallback_payload = json.loads(json.dumps(SISTEM_DURUMU))
                fallback_payload["pending_kupons"] = []
                fallback_payload["performance"] = dict(_DEFAULT_PERFORMANCE)
                try:
                    body = json.dumps(fallback_payload, ensure_ascii=False).encode("utf-8")
                    self._send_bytes(200, "application/json; charset=utf-8", body)
                except (TypeError, ValueError):
                    self._send_bytes(500, "application/json; charset=utf-8", b"{}")
            return

        if route == "/api/finance_metrics":
            try:
                self._send_json(200, _build_finance_metrics_payload())
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"finance_metrics serialize failed | {exc}")
                self._send_json(200, {"status": "online", "roi": 0.0, "net_kar": 0.0})
            return

        if route == "/api/risk_settings":
            self._send_json(200, {"ok": True, "sliders": build_slider_payload(), "values": get_operator_risk_settings()})
            return

        self._send_bytes(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0]

        if route == "/api/force_add_ip":
            if not self._is_trusted_client():
                _emit_operator_diag(
                    f"Teşhis: Yetkisiz Terminal Erişimi - IP: {self._client_ip()}"
                )
                self._send_json(403, {"ok": False, "error": "forbidden"})
                return

            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            ip_raw = body.get("ip")
            if not isinstance(ip_raw, str) or not ip_raw.strip():
                self._send_json(400, {"ok": False, "error": "ip must be a non-empty string"})
                return

            normalized_ip = ip_raw.strip()
            if not add_authorized_ip(normalized_ip):
                self._send_json(500, {"ok": False, "error": "ip authorization failed"})
                return

            _emit_operator_diag(f"Sistem: {normalized_ip} manuel olarak yetkilendirildi")
            self._send_json(200, {"ok": True, "ip": normalized_ip})
            return

        if not self._enforce_ip_policy(route):
            return

        if route == "/api/toggle_scan":
            try:
                scan_enabled = _toggle_scan_enabled()
                self._send_json(200, {"ok": True, "scan_enabled": scan_enabled})
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"toggle_scan failed | {exc}")
                self._send_json(500, {"ok": False, "error": "toggle_scan failed"})
            return

        if route == "/api/toggle_live_alerts":
            try:
                new_state = set_live_alerts_enabled(not is_live_alerts_enabled())
                self._send_json(200, {"ok": True, "live_alerts_enabled": new_state})
            except (TypeError, ValueError, OSError) as exc:
                _emit_kupon_api_diag(f"toggle_live_alerts failed | {exc}")
                self._send_json(500, {"ok": False, "error": "toggle_live_alerts failed"})
            return

        if route == "/api/toggle_side_markets":
            try:
                new_state = set_side_markets_enabled(not is_side_markets_enabled())
                self._send_json(200, {
                    "ok": True,
                    "side_markets_enabled": new_state,
                    "side_markets_effective": is_side_markets_effective(),
                    "side_markets_frozen": is_side_markets_frozen(),
                })
            except (TypeError, ValueError, OSError) as exc:
                _emit_kupon_api_diag(f"toggle_side_markets failed | {exc}")
                self._send_json(500, {"ok": False, "error": "toggle_side_markets failed"})
            return

        if route == "/api/update_kasa":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            bakiye_raw = body.get("bakiye")
            if isinstance(bakiye_raw, bool) or not isinstance(bakiye_raw, (int, float)):
                _emit_kupon_api_diag("update_kasa | bakiye must be numeric")
                self._send_json(400, {"ok": False, "error": "bakiye must be numeric"})
                return

            yeni_bakiye = float(bakiye_raw)
            if yeni_bakiye <= 0.0:
                _emit_kupon_api_diag("update_kasa | bakiye must be > 0")
                self._send_json(400, {"ok": False, "error": "bakiye must be > 0"})
                return

            try:
                aciklama = "Operator Paneli Guncellemesi"
                saved = save_bakiye(yeni_bakiye, aciklama)
                if not saved:
                    self._send_json(500, {"ok": False, "error": "database write failed"})
                    return
                update_sistem_durumu(total_kasa=yeni_bakiye)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "total_kasa": yeni_bakiye,
                        "budget_configured": True,
                        "budget_required": False,
                        "scan_enabled": _snapshot_status()["scan_enabled"],
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"update_kasa failed | {exc}")
                self._send_json(500, {"ok": False, "error": "update_kasa failed"})
            return

        if route == "/api/risk_settings":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            try:
                values = update_operator_risk_settings(body, persist=True)
                update_sistem_durumu(
                    risk_per_trade=get_operator_risk_per_trade(),
                    ev_threshold=get_action_ev_threshold(),
                )
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "values": values,
                        "sliders": build_slider_payload(),
                        "risk_presets": build_risk_presets_payload(),
                        "active_risk_preset": detect_active_risk_preset(),
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"risk_settings failed | {exc}")
                self._send_json(500, {"ok": False, "error": "risk_settings failed"})
            return

        if route == "/api/risk_preset":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            preset_raw = body.get("preset")
            if not isinstance(preset_raw, str) or not preset_raw.strip():
                self._send_json(400, {"ok": False, "error": "preset must be low, medium, or high"})
                return

            preset_id = preset_raw.strip().casefold()
            values = apply_risk_preset(preset_id, persist=True)
            if values is None:
                self._send_json(400, {"ok": False, "error": "unknown preset"})
                return

            update_sistem_durumu(
                risk_per_trade=get_operator_risk_per_trade(),
                ev_threshold=get_action_ev_threshold(),
            )
            self._send_json(
                200,
                {
                    "ok": True,
                    "preset": preset_id,
                    "values": values,
                    "sliders": build_slider_payload(),
                    "risk_presets": build_risk_presets_payload(),
                    "active_risk_preset": detect_active_risk_preset(),
                },
            )
            return

        if route == "/api/experimental_features":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            try:
                state = update_experimental_features(body)
                panel = build_experimental_panel_payload()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "state": state,
                        "experimental_features": panel["features"],
                        "experimental": panel,
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"experimental_features failed | {exc}")
                self._send_json(500, {"ok": False, "error": "experimental_features failed"})
            return

        if route == "/api/experimental_mode":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            mode_raw = body.get("mode")
            if not isinstance(mode_raw, str) or not mode_raw.strip():
                self._send_json(400, {"ok": False, "error": "mode must be shadow or live"})
                return

            try:
                mode = set_experimental_mode(mode_raw.strip())
                panel = build_experimental_panel_payload()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "mode": mode,
                        "experimental": panel,
                        "experimental_features": panel["features"],
                    },
                )
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"experimental_mode failed | {exc}")
                self._send_json(500, {"ok": False, "error": "experimental_mode failed"})
            return

        if route == "/api/notify_frequency":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            level_raw = body.get("level")
            if not isinstance(level_raw, str) or not level_raw.strip():
                self._send_json(400, {"ok": False, "error": "level must be 1, 2, 3, 4, or 5"})
                return

            try:
                payload = apply_notify_frequency_level(level_raw.strip(), persist_risk=True)
                update_sistem_durumu(
                    risk_per_trade=get_operator_risk_per_trade(),
                    ev_threshold=get_action_ev_threshold(),
                )
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "notify_frequency": payload,
                        "risk_settings": build_slider_payload(),
                        "risk_presets": build_risk_presets_payload(),
                        "active_risk_preset": detect_active_risk_preset(),
                        "scan_leagues": build_scan_leagues_payload(),
                    },
                )
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"notify_frequency failed | {exc}")
                self._send_json(500, {"ok": False, "error": "notify_frequency failed"})
            return

        if route == "/api/hero_mode":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            if "enabled" not in body:
                self._send_json(400, {"ok": False, "error": "enabled must be true or false"})
                return

            enabled = bool(body.get("enabled"))
            try:
                set_hero_mode_enabled(enabled)
                if enabled:
                    bootstrap_hero_profile()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "hero": build_hero_panel_payload(),
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"hero_mode failed | {exc}")
                self._send_json(500, {"ok": False, "error": "hero_mode failed"})
            return

        if route == "/api/hero_profile":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            level_raw = body.get("level")
            if not isinstance(level_raw, str) or not level_raw.strip():
                self._send_json(400, {"ok": False, "error": "level must be 1, 2, 3, 4, or 5"})
                return

            try:
                if not is_hero_mode_enabled():
                    self._send_json(400, {"ok": False, "error": "hero mode is disabled"})
                    return
                apply_hero_profile_level(level_raw.strip())
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "hero": build_hero_panel_payload(),
                    },
                )
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"hero_profile failed | {exc}")
                self._send_json(500, {"ok": False, "error": "hero_profile failed"})
            return

        if route == "/api/scan_leagues":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            try:
                values = update_scan_league_settings(body)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "values": values,
                        "scan_leagues": annotate_scan_leagues_payload(
                            build_scan_leagues_payload()
                        ),
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"scan_leagues failed | {exc}")
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if route == "/api/reset_tracking":
            body = self._read_json_body() or {}
            bakiye_raw = body.get("bakiye")
            bakiye: float | None = None
            if bakiye_raw is not None:
                if isinstance(bakiye_raw, bool) or not isinstance(bakiye_raw, (int, float)):
                    self._send_json(400, {"ok": False, "error": "bakiye must be numeric"})
                    return
                bakiye = float(bakiye_raw)
                if bakiye <= 0.0:
                    self._send_json(400, {"ok": False, "error": "bakiye must be > 0"})
                    return

            try:
                result = reset_operator_tracking(bakiye=bakiye)
                if not result.get("ok"):
                    self._send_json(500, {"ok": False, "error": result.get("error", "reset failed")})
                    return

                total_kasa = float(result["total_kasa"])
                update_sistem_durumu(
                    total_kasa=total_kasa,
                    pending_kupons=[],
                    alarm_candidate_count=0,
                    matches=[],
                )
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "total_kasa": total_kasa,
                        "cleared": result.get("cleared", {}),
                        "performance": result.get("performance", {}),
                        "pending_kupons": [],
                        "recent_kupons": [],
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"reset_tracking failed | {exc}")
                self._send_json(500, {"ok": False, "error": "reset_tracking failed"})
            return

        if route == "/api/recalibrate_kasa":
            body = self._read_json_body() or {}
            baseline_raw = body.get("baseline", body.get("bakiye"))
            if isinstance(baseline_raw, bool) or not isinstance(baseline_raw, (int, float)):
                self._send_json(400, {"ok": False, "error": "baseline must be numeric"})
                return
            baseline = float(baseline_raw)
            if baseline <= 0.0:
                self._send_json(400, {"ok": False, "error": "baseline must be > 0"})
                return

            aciklama_raw = body.get("aciklama")
            aciklama = str(aciklama_raw).strip() if isinstance(aciklama_raw, str) and aciklama_raw.strip() else None

            try:
                result = recalibrate_kasa_from_baseline(baseline, aciklama=aciklama)
                if not result.get("ok"):
                    self._send_json(500, {"ok": False, "error": result.get("error", "recalibrate failed")})
                    return

                total_kasa = round(float(result["total_kasa"]), 2)
                update_sistem_durumu(
                    total_kasa=total_kasa,
                    performance=result.get("performance"),
                )
                self._send_json(200, result)
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"recalibrate_kasa failed | {exc}")
                self._send_json(500, {"ok": False, "error": "recalibrate_kasa failed"})
            return

        if route == "/api/result_kupon":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            kupon_id_raw = body.get("kupon_id")
            sonuc_raw = body.get("sonuc")

            if isinstance(kupon_id_raw, bool) or not isinstance(kupon_id_raw, int) or kupon_id_raw <= 0:
                _emit_kupon_api_diag("result_kupon | kupon_id must be a positive int")
                self._send_json(400, {"ok": False, "error": "kupon_id must be a positive int"})
                return

            if not isinstance(sonuc_raw, str):
                _emit_kupon_api_diag("result_kupon | sonuc must be a string")
                self._send_json(400, {"ok": False, "error": "sonuc must be a string"})
                return

            normalized_sonuc = sonuc_raw.strip().upper()
            if normalized_sonuc not in _VALID_RESULTS:
                _emit_kupon_api_diag("result_kupon | sonuc must be WON or LOST")
                self._send_json(400, {"ok": False, "error": "sonuc must be WON or LOST"})
                return

            try:
                settled = result_kupon(int(kupon_id_raw), normalized_sonuc)
                if not settled:
                    self._send_json(500, {"ok": False, "error": "kupon settlement failed"})
                    return

                new_kasa = get_latest_bakiye()
                update_sistem_durumu(total_kasa=new_kasa, performance=_load_performance_stats())
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "kupon_id": int(kupon_id_raw),
                        "sonuc": normalized_sonuc,
                        "total_kasa": new_kasa,
                        "pending_kupons": _fetch_pending_kupons(),
                        "performance": _load_performance_stats(),
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"result_kupon failed | {exc}")
                self._send_json(500, {"ok": False, "error": "result_kupon failed"})
            return

        if route == "/api/play_signal":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            match_id_raw = body.get("match_id")
            if not isinstance(match_id_raw, str) or not match_id_raw.strip():
                self._send_json(400, {"ok": False, "error": "match_id must be a non-empty string"})
                return

            alinan_val: float | None = None
            alinan_raw = body.get("alinan_oran")
            if alinan_raw is not None:
                if isinstance(alinan_raw, bool) or not isinstance(alinan_raw, (int, float)) or float(alinan_raw) <= 1.0:
                    self._send_json(400, {"ok": False, "error": "alinan_oran must be > 1.0"})
                    return
                alinan_val = float(alinan_raw)

            stake_val: float | None = None
            stake_raw = body.get("stake")
            if stake_raw is not None:
                if isinstance(stake_raw, bool) or not isinstance(stake_raw, (int, float)) or float(stake_raw) <= 0.0:
                    self._send_json(400, {"ok": False, "error": "stake must be > 0"})
                    return
                stake_val = float(stake_raw)

            try:
                from notifiers.telegram_worker import play_recommendation_from_panel

                result = play_recommendation_from_panel(
                    match_id_raw.strip(), alinan_oran=alinan_val, stake=stake_val
                )
                if not isinstance(result, dict) or not result.get("ok"):
                    reason = result.get("reason", "play failed") if isinstance(result, dict) else "play failed"
                    self._send_json(200, {"ok": False, "error": str(reason)})
                    return

                new_kasa = get_latest_bakiye()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "mac_adi": result.get("mac_adi", ""),
                        "market": result.get("market", ""),
                        "stake": result.get("stake"),
                        "alinan_oran": result.get("alinan_oran"),
                        "total_kasa": new_kasa,
                        "pending_kupons": _fetch_pending_kupons(),
                        "recent_kupons": get_recent_kupons(limit=30),
                        "recommendations": _fetch_recommendations(),
                        "performance": _load_performance_stats(),
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"play_signal failed | {exc}")
                self._send_json(500, {"ok": False, "error": "play_signal failed"})
            return

        if route == "/api/skip_signal":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            match_id_raw = body.get("match_id")
            if not isinstance(match_id_raw, str) or not match_id_raw.strip():
                self._send_json(400, {"ok": False, "error": "match_id must be a non-empty string"})
                return

            try:
                from notifiers.telegram_worker import skip_recommendation_from_panel

                result = skip_recommendation_from_panel(match_id_raw.strip())
                self._send_json(
                    200,
                    {
                        "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
                        "recommendations": _fetch_recommendations(),
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"skip_signal failed | {exc}")
                self._send_json(500, {"ok": False, "error": "skip_signal failed"})
            return

        if route == "/api/set_played_odds":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "error": "invalid json body"})
                return

            kupon_id_raw = body.get("kupon_id")
            alinan_raw = body.get("alinan_oran")
            if isinstance(kupon_id_raw, bool) or not isinstance(kupon_id_raw, int) or kupon_id_raw <= 0:
                self._send_json(400, {"ok": False, "error": "kupon_id must be a positive int"})
                return
            if isinstance(alinan_raw, bool) or not isinstance(alinan_raw, (int, float)) or float(alinan_raw) <= 1.0:
                self._send_json(400, {"ok": False, "error": "alinan_oran must be > 1.0"})
                return

            try:
                updated = set_kupon_alinan_oran(int(kupon_id_raw), float(alinan_raw))
                if not updated:
                    self._send_json(200, {"ok": False, "error": "kupon_bulunamadi"})
                    return
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "kupon_id": int(kupon_id_raw),
                        "alinan_oran": round(float(alinan_raw), 2),
                        "recent_kupons": get_recent_kupons(limit=30),
                        "pending_kupons": _fetch_pending_kupons(),
                    },
                )
            except (TypeError, ValueError) as exc:
                _emit_kupon_api_diag(f"set_played_odds failed | {exc}")
                self._send_json(500, {"ok": False, "error": "set_played_odds failed"})
            return

        self._send_json(404, {"ok": False, "error": "not found"})


def start_web_server() -> None:
    global _SERVER, _SERVER_THREAD

    if _SERVER is not None:
        return

    try:
        bootstrap_hero_mode()
        bootstrap_notify_frequency_profile()
        bootstrap_experimental_mode()
        if is_hero_mode_enabled():
            bootstrap_hero_profile()
        # Ham risk ince ayari (varsa) EN SON bindirilir; seviye/preset uygulandiktan
        # sonra kullanicinin elle ayarini geri yukler. Dosya yoksa hicbir sey yapmaz.
        bootstrap_operator_risk_settings()
    except (TypeError, ValueError, OSError) as exc:
        _emit_operator_diag(f"profile bootstrap failed | {exc}")

    try:
        _SERVER = ThreadingHTTPServer((_WEB_HOST, _WEB_PORT), _OperatorPanelHandler)
        _SERVER.allow_reuse_address = True
        _SERVER.daemon_threads = True
        _SERVER_THREAD = threading.Thread(
            target=_SERVER.serve_forever,
            name="SQE-WebServer",
            daemon=True,
        )
        _SERVER_THREAD.start()
    except OSError as exc:
        _emit_operator_diag(f"bind failed | {_WEB_HOST or '0.0.0.0'}:{_WEB_PORT} | {exc}")
        _SERVER = None
        _SERVER_THREAD = None
        raise


def stop_web_server() -> None:
    global _SERVER, _SERVER_THREAD

    try:
        if _SERVER is not None:
            _SERVER.shutdown()
            _SERVER.server_close()
    except OSError as exc:
        _emit_operator_diag(f"shutdown failed | {exc}")
    finally:
        _SERVER = None

    if _SERVER_THREAD is not None:
        try:
            _SERVER_THREAD.join(timeout=3.0)
        except RuntimeError as exc:
            _emit_operator_diag(f"thread join failed | {exc}")
        _SERVER_THREAD = None

    update_sistem_durumu(
        is_live=False,
        scan_enabled=False,
        pending_kupons=[],
        performance=dict(_DEFAULT_PERFORMANCE),
    )

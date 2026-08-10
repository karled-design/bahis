from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from config.settings import TELEGRAM_TOKEN

__all__ = (
    "DEFAULT_PANEL_PORT",
    "MOTOR_PROCESS_PATTERN",
    "drain_telegram_update_queue",
    "full_telegram_conflict_repair",
    "kill_panel_port_listeners",
    "kill_stale_motor_processes",
    "reset_telegram_api_session",
)

DEFAULT_PANEL_PORT = 8765
MOTOR_PROCESS_PATTERN = "bahis/main.py"
_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_QUEUE_DRAIN_ROUNDS = 20


def _telegram_get(url: str) -> dict[str, Any] | None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Teşhis: Telegram API isteği başarısız | {exc}", file=sys.stderr)
        return None

    try:
        payload: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        print("Teşhis: Telegram API geçersiz yanıt döndü", file=sys.stderr)
        return None

    if payload.get("ok") is not True:
        print(
            f"Teşhis: Telegram API hatası | {payload.get('description', 'ok=false')}",
            file=sys.stderr,
        )
        return None

    return payload


def _parse_pid_lines(text: str) -> list[int]:
    pids: list[int] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _signal_pids(pids: list[int], sig: signal.Signals) -> int:
    stopped = 0
    for pid in pids:
        if pid <= 0 or pid == os.getpid():
            continue
        try:
            os.kill(pid, sig)
            stopped += 1
        except OSError:
            continue
    return stopped


def kill_stale_motor_processes(*, exclude_current: bool = True) -> int:
    """Stop other bahis/main.py processes (409 conflict source)."""
    current_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", MOTOR_PROCESS_PATTERN],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Teşhis: Motor süreç taraması başarısız | {exc}", file=sys.stderr)
        return 0

    pids = _parse_pid_lines(result.stdout)
    if exclude_current:
        pids = [pid for pid in pids if pid != current_pid]
    if not pids:
        print("Teşhis: Çakışan motor süreci bulunamadı.")
        return 0

    stopped = _signal_pids(pids, signal.SIGTERM)
    print(f"Teşhis: {stopped} motor süreci durduruldu (SIGTERM).")
    return stopped


def kill_panel_port_listeners(port: int = DEFAULT_PANEL_PORT) -> int:
    """Free panel port so a single web_server instance can bind."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Teşhis: Port {port} taraması başarısız | {exc}", file=sys.stderr)
        return 0

    pids = _parse_pid_lines(result.stdout)
    pids = [pid for pid in pids if pid != os.getpid()]
    if not pids:
        print(f"Teşhis: Port {port} zaten bos.")
        return 0

    stopped = _signal_pids(pids, signal.SIGKILL)
    print(f"Teşhis: Port {port} uzerinde {stopped} süreç kapatildi.")
    return stopped


def reset_telegram_api_session(*, drop_pending_updates: bool = True) -> bool:
    if not TELEGRAM_TOKEN:
        print("Teşhis: TELEGRAM_TOKEN tanimli degil", file=sys.stderr)
        return False

    query = urllib.parse.urlencode(
        {"drop_pending_updates": "true" if drop_pending_updates else "false"}
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook?{query}"
    if _telegram_get(url) is None:
        return False
    return True


def drain_telegram_update_queue() -> None:
    if not TELEGRAM_TOKEN:
        return

    offset = 0
    for _ in range(_MAX_QUEUE_DRAIN_ROUNDS):
        query = urllib.parse.urlencode({"offset": offset, "timeout": 0, "limit": 100})
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?{query}"
        payload = _telegram_get(url)
        if payload is None:
            return

        updates = payload.get("result")
        if not isinstance(updates, list) or not updates:
            return

        last_update_id = None
        for update in updates:
            if isinstance(update, dict):
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    last_update_id = update_id

        if last_update_id is None:
            return

        offset = last_update_id + 1


def full_telegram_conflict_repair(
    *,
    stop_local_motor: bool = True,
    free_panel_port: bool = True,
    panel_port: int = DEFAULT_PANEL_PORT,
) -> bool:
    """Full 409 repair: local processes + webhook/queue reset."""
    if stop_local_motor:
        kill_stale_motor_processes(exclude_current=True)
    if free_panel_port:
        kill_panel_port_listeners(port=panel_port)

    if not reset_telegram_api_session(drop_pending_updates=True):
        return False

    drain_telegram_update_queue()
    print("Teşhis: Telegram oturumu sifirlandi (webhook + kuyruk).")
    return True

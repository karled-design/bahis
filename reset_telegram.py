from __future__ import annotations

import argparse
import sys

from notifiers.telegram_reset import (
    DEFAULT_PANEL_PORT,
    drain_telegram_update_queue,
    full_telegram_conflict_repair,
    kill_panel_port_listeners,
    kill_stale_motor_processes,
    reset_telegram_api_session,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQE-V1 Telegram cakisma (409) onarici — motor/port temizligi + API sifirlama.",
    )
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Yalnizca webhook/kuyruk temizligi (calisan motoru oldurmez).",
    )
    parser.add_argument(
        "--no-port-kill",
        action="store_true",
        help="8765 portunu bosaltma adimini atla.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PANEL_PORT,
        help=f"Panel portu (varsayilan {DEFAULT_PANEL_PORT}).",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.api_only:
        if not reset_telegram_api_session(drop_pending_updates=True):
            return 1
        drain_telegram_update_queue()
        print("Teşhis: Telegram Webhook ve kuyruk temizlendi (api-only).")
        return 0

    ok = full_telegram_conflict_repair(
        stop_local_motor=True,
        free_panel_port=not args.no_port_kill,
        panel_port=args.port,
    )
    if not ok:
        return 1

    print(
        "Teşhis: Tam onarim tamamlandi. Simdi tek motor baslatin:\n"
        "  PYTHONPATH=. python bahis/main.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

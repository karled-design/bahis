from __future__ import annotations

import unittest
from unittest import mock

from notifiers import telegram_reset


class TelegramResetTests(unittest.TestCase):
    def test_full_repair_runs_process_port_and_api_steps(self) -> None:
        with mock.patch.object(telegram_reset, "kill_stale_motor_processes", return_value=1) as kill_motor:
            with mock.patch.object(telegram_reset, "kill_panel_port_listeners", return_value=1) as kill_port:
                with mock.patch.object(telegram_reset, "reset_telegram_api_session", return_value=True) as reset_api:
                    with mock.patch.object(telegram_reset, "drain_telegram_update_queue") as drain:
                        ok = telegram_reset.full_telegram_conflict_repair()

        self.assertTrue(ok)
        kill_motor.assert_called_once()
        kill_port.assert_called_once()
        reset_api.assert_called_once_with(drop_pending_updates=True)
        drain.assert_called_once()

    def test_api_only_skips_process_kill(self) -> None:
        with mock.patch.object(telegram_reset, "kill_stale_motor_processes") as kill_motor:
            with mock.patch.object(telegram_reset, "reset_telegram_api_session", return_value=True):
                with mock.patch.object(telegram_reset, "drain_telegram_update_queue"):
                    ok = telegram_reset.full_telegram_conflict_repair(
                        stop_local_motor=False,
                        free_panel_port=False,
                    )

        self.assertTrue(ok)
        kill_motor.assert_not_called()


if __name__ == "__main__":
    unittest.main()

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


class TelegramConflictBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        from notifiers import telegram_worker

        self.worker = telegram_worker
        telegram_worker._CONFLICT_HEAL_COUNT = 0
        telegram_worker._CONFLICT_ESCALATION_NOTIFIED = False
        self.addCleanup(setattr, telegram_worker, "_CONFLICT_HEAL_COUNT", 0)
        self.addCleanup(setattr, telegram_worker, "_CONFLICT_ESCALATION_NOTIFIED", False)

    def test_backoff_grows_and_is_capped(self) -> None:
        self.assertEqual(self.worker._conflict_backoff_seconds(1), 3.0)
        self.assertEqual(self.worker._conflict_backoff_seconds(2), 6.0)
        self.assertEqual(self.worker._conflict_backoff_seconds(3), 12.0)
        self.assertEqual(self.worker._conflict_backoff_seconds(20), 60.0)

    def test_only_first_conflict_resets_session(self) -> None:
        with mock.patch.object(self.worker, "time") as fake_time:
            fake_time.sleep.return_value = None
            with mock.patch.object(telegram_reset, "reset_telegram_api_session", return_value=True) as reset_api:
                with mock.patch.object(telegram_reset, "drain_telegram_update_queue") as drain:
                    for _ in range(4):
                        self.worker._self_heal_telegram_conflict()

        reset_api.assert_called_once()
        drain.assert_called_once()

    def test_escalation_diag_printed_once(self) -> None:
        with mock.patch.object(self.worker, "time") as fake_time:
            fake_time.sleep.return_value = None
            with mock.patch.object(telegram_reset, "reset_telegram_api_session", return_value=True):
                with mock.patch.object(telegram_reset, "drain_telegram_update_queue"):
                    with mock.patch("builtins.print") as printed:
                        for _ in range(8):
                            self.worker._self_heal_telegram_conflict()

        escalations = [
            call
            for call in printed.call_args_list
            if call.args and "baska bir motor" in str(call.args[0])
        ]
        self.assertEqual(len(escalations), 1)

    def test_successful_poll_clears_conflict_state(self) -> None:
        self.worker._CONFLICT_HEAL_COUNT = 7
        self.worker._CONFLICT_ESCALATION_NOTIFIED = True
        self.worker._note_telegram_transport_success()
        self.assertEqual(self.worker._CONFLICT_HEAL_COUNT, 0)
        self.assertFalse(self.worker._CONFLICT_ESCALATION_NOTIFIED)


if __name__ == "__main__":
    unittest.main()

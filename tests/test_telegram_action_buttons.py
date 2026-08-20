from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from core import manual_play_ledger, notify_snooze
from notifiers import telegram_worker


class NotifySnoozeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(
            notify_snooze,
            "SNOOZE_STATE_PATH",
            Path(self._tmp.name) / "notify_snooze.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_snoozed_match_is_silenced_until_due(self) -> None:
        notify_snooze.snooze_match("mac_ms1", seconds=600, now=1_000.0)

        self.assertTrue(notify_snooze.is_snoozed("mac_ms1", now=1_100.0))
        self.assertFalse(notify_snooze.is_snoozed("mac_ms1", now=1_700.0))

    def test_expired_snooze_is_consumed_once(self) -> None:
        notify_snooze.snooze_match("mac_ms1", seconds=600, now=1_000.0)

        self.assertFalse(notify_snooze.consume_expired_snooze("mac_ms1", now=1_100.0))
        self.assertTrue(notify_snooze.consume_expired_snooze("mac_ms1", now=1_700.0))
        self.assertFalse(notify_snooze.consume_expired_snooze("mac_ms1", now=1_800.0))


class FixtureGuardSnoozeTests(unittest.TestCase):
    def setUp(self) -> None:
        from core import fixture_notify_guard

        self.guard = fixture_notify_guard
        kupon_patch = mock.patch.object(
            fixture_notify_guard, "has_any_kupon_for_fixture", return_value=False
        )
        kupon_patch.start()
        self.addCleanup(kupon_patch.stop)
        notified_patch = mock.patch.object(
            fixture_notify_guard, "was_fixture_recently_notified", return_value=True
        )
        notified_patch.start()
        self.addCleanup(notified_patch.stop)

    def test_snoozed_fixture_is_skipped(self) -> None:
        with mock.patch.object(self.guard, "is_snoozed", return_value=True):
            skip, reason = self.guard.should_skip_fixture_telegram(
                "arsenal - chelsea|MS1",
                "Arsenal - Chelsea",
                "MS1",
                2.10,
                odds_change_bypass_pct=0.05,
            )

        self.assertTrue(skip)
        self.assertEqual(reason, "fixture_ertelendi")

    def test_expired_snooze_bypasses_dedup_once(self) -> None:
        with mock.patch.object(self.guard, "is_snoozed", return_value=False):
            with mock.patch.object(
                self.guard, "consume_expired_snooze", return_value=True
            ):
                skip, reason = self.guard.should_skip_fixture_telegram(
                    "arsenal - chelsea|MS1",
                    "Arsenal - Chelsea",
                    "MS1",
                    2.10,
                    odds_change_bypass_pct=0.05,
                )

        self.assertFalse(skip)
        self.assertEqual(reason, "")


class InlineKeyboardTests(unittest.TestCase):
    def _labels(self, keyboard: dict[str, object]) -> list[str]:
        rows = keyboard["inline_keyboard"]
        assert isinstance(rows, list)
        return [str(button["text"]) for row in rows for button in row]

    def _callbacks(self, keyboard: dict[str, object]) -> list[str]:
        rows = keyboard["inline_keyboard"]
        assert isinstance(rows, list)
        return [str(button["callback_data"]) for row in rows for button in row]

    def test_measurement_mode_keyboard_cannot_open_coupon(self) -> None:
        with mock.patch.object(telegram_worker, "_is_no_money_mode", return_value=True):
            keyboard = telegram_worker._build_inline_keyboard("arsenal_ms1", 120.0)

        callbacks = self._callbacks(keyboard)
        self.assertEqual(callbacks, ["mark_arsenal_ms1", "snooze_arsenal_ms1"])
        self.assertTrue(all(not data.startswith("play_") for data in callbacks))
        self.assertIn("✅ Oynadım", self._labels(keyboard))
        self.assertIn("⏰ Ertele", self._labels(keyboard))

    def test_real_mode_keyboard_keeps_play_and_adds_snooze(self) -> None:
        with mock.patch.object(telegram_worker, "_is_no_money_mode", return_value=False):
            keyboard = telegram_worker._build_inline_keyboard("arsenal_ms1", 120.0)

        self.assertEqual(
            self._callbacks(keyboard),
            ["play_arsenal_ms1_120.0", "snooze_arsenal_ms1", "skip_arsenal_ms1"],
        )

    def test_missing_stake_falls_back_to_mark_button(self) -> None:
        with mock.patch.object(telegram_worker, "_is_no_money_mode", return_value=False):
            keyboard = telegram_worker._build_inline_keyboard("arsenal_ms1", None)

        self.assertEqual(
            self._callbacks(keyboard), ["mark_arsenal_ms1", "snooze_arsenal_ms1"]
        )


class CallbackHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for module, attribute, filename in (
            (notify_snooze, "SNOOZE_STATE_PATH", "notify_snooze.json"),
            (manual_play_ledger, "MANUAL_PLAY_STATE_PATH", "manual_play.json"),
        ):
            patcher = mock.patch.object(
                module, attribute, Path(self._tmp.name) / filename
            )
            patcher.start()
            self.addCleanup(patcher.stop)

        clear_patch = mock.patch.object(telegram_worker, "_clear_inline_buttons")
        self.clear_buttons = clear_patch.start()
        self.addCleanup(clear_patch.stop)
        notice_patch = mock.patch.object(telegram_worker, "_send_operator_notice")
        self.notice = notice_patch.start()
        self.addCleanup(notice_patch.stop)

    def test_snooze_callback_silences_match(self) -> None:
        handled = telegram_worker._handle_snooze_callback({}, "snooze_arsenal_ms1")

        self.assertTrue(handled)
        self.assertTrue(notify_snooze.is_snoozed("arsenal_ms1"))
        self.clear_buttons.assert_called_once()

    def test_mark_callback_records_once_and_opens_no_coupon(self) -> None:
        with mock.patch.object(telegram_worker, "add_kupon") as add_kupon:
            with mock.patch.object(telegram_worker, "save_bakiye") as save_bakiye:
                first = telegram_worker._handle_mark_callback({}, "mark_arsenal_ms1")
                second = telegram_worker._handle_mark_callback({}, "mark_arsenal_ms1")

        self.assertTrue(first)
        self.assertTrue(second)
        add_kupon.assert_not_called()
        save_bakiye.assert_not_called()
        self.assertEqual(len(manual_play_ledger.marked_plays()), 1)

    def test_unknown_callback_is_rejected(self) -> None:
        self.assertFalse(telegram_worker._handle_snooze_callback({}, "snooze_"))
        self.assertFalse(telegram_worker._handle_mark_callback({}, "mark_"))


if __name__ == "__main__":
    unittest.main()

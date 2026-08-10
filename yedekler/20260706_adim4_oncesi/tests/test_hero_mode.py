from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.hero_mode import (
    bootstrap_hero_mode,
    build_hero_panel_payload,
    is_hero_mode_enabled,
    set_hero_mode_enabled,
)
from core.hero_profile import apply_hero_profile_level, bootstrap_hero_profile, DEFAULT_HERO_PROFILE_LEVEL


class HeroModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmpdir.name) / "hero_mode.json"
        self._patch = mock.patch("core.hero_mode._PERSIST_PATH", self._state_path)
        self._patch.start()
        self._env_patch = mock.patch("core.hero_mode._ENV_HERO_DEFAULT", False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_env_default_when_no_file(self) -> None:
        self.assertFalse(is_hero_mode_enabled())

    def test_persisted_enabled_overrides_env(self) -> None:
        set_hero_mode_enabled(True)
        self.assertTrue(is_hero_mode_enabled())
        self.assertTrue(bootstrap_hero_mode())

    def test_toggle_off_persists(self) -> None:
        set_hero_mode_enabled(True)
        set_hero_mode_enabled(False)
        self.assertFalse(is_hero_mode_enabled())

    def test_build_panel_payload_contains_sections(self) -> None:
        bootstrap_hero_profile()
        apply_hero_profile_level(DEFAULT_HERO_PROFILE_LEVEL)
        set_hero_mode_enabled(True)
        payload = build_hero_panel_payload()
        self.assertTrue(payload["enabled"])
        self.assertIn("profile", payload)
        self.assertIn("daily", payload)
        self.assertIn("weekly", payload)
        self.assertIn("win_rate_last_20", payload)
        self.assertIn("measurement", payload)


if __name__ == "__main__":
    unittest.main()

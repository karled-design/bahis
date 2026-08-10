from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from config.settings import resolve_sqe_db_path
from database import db_manager
from database.db_manager import get_db_path, init_db


class DbPathResolutionTests(unittest.TestCase):
    def test_pilot_mode_defaults_to_pilot_db(self) -> None:
        root = Path("/tmp/sqe-test-root")
        path = resolve_sqe_db_path(project_root=root, pilot_mode=True, env_value="")
        self.assertEqual(path, root / "database" / "sqe_pilot.db")

    def test_live_mode_defaults_to_storage_db(self) -> None:
        root = Path("/tmp/sqe-test-root")
        path = resolve_sqe_db_path(project_root=root, pilot_mode=False, env_value="")
        self.assertEqual(path, root / "database" / "sqe_storage.db")

    def test_env_override_absolute_path(self) -> None:
        path = resolve_sqe_db_path(
            project_root=Path("/ignored"),
            pilot_mode=True,
            env_value="/custom/sqe.db",
        )
        self.assertEqual(path, Path("/custom/sqe.db"))

    def test_env_override_relative_path(self) -> None:
        root = Path("/tmp/sqe-test-root")
        path = resolve_sqe_db_path(
            project_root=root,
            pilot_mode=True,
            env_value="database/custom_test.db",
        )
        self.assertEqual(path, root / "database" / "custom_test.db")

    def test_get_db_path_matches_module_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = Path(tmp) / "isolated.db"
            previous = db_manager._DB_PATH
            db_manager._DB_PATH = isolated
            try:
                self.assertEqual(get_db_path(), isolated)
                self.assertTrue(init_db())
            finally:
                db_manager._DB_PATH = previous


if __name__ == "__main__":
    unittest.main()

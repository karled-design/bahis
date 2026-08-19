from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _PROJECT_ROOT / "tools" / "motor_ctl.sh"


def _run(script: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), command],
        capture_output=True,
        text=True,
        timeout=60,
    )


class MotorCtlTests(unittest.TestCase):
    """Betigin durum/temizlik mantigi (motoru gercekten baslatmadan)."""

    def setUp(self) -> None:
        # Izole kopya: gercek database/motor.pid dosyasina dokunmayalim.
        self._tmp = tempfile.mkdtemp(prefix="motor_ctl_")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        root = Path(self._tmp)
        (root / "tools").mkdir()
        (root / "database").mkdir()
        self.script = root / "tools" / "motor_ctl.sh"
        shutil.copy(_SCRIPT, self.script)
        self.pid_file = root / "database" / "motor.pid"

    def test_durum_kapali_when_no_pid_file(self) -> None:
        result = _run(self.script, "durum")
        self.assertEqual(result.returncode, 1)
        self.assertIn("KAPALI", result.stdout)

    def test_stale_pid_file_is_cleaned(self) -> None:
        # Yasamayan PID: dosya bayat sayilir ve silinir, aksi halde "calisiyor"
        # sanip motor bir daha hic baslamaz.
        self.pid_file.write_text("999999", encoding="utf-8")
        result = _run(self.script, "durum")
        self.assertIn("KAPALI", result.stdout)
        self.assertFalse(self.pid_file.exists())

    def test_foreign_pid_is_not_claimed(self) -> None:
        # Yasayan ama bize ait olmayan PID (kendi test surecimiz) sahiplenilmemeli.
        self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        result = _run(self.script, "durum")
        self.assertIn("KAPALI", result.stdout)
        self.assertFalse(self.pid_file.exists())

    def test_garbage_pid_file_is_cleaned(self) -> None:
        self.pid_file.write_text("bozuk", encoding="utf-8")
        result = _run(self.script, "durum")
        self.assertIn("KAPALI", result.stdout)
        self.assertFalse(self.pid_file.exists())

    def test_durdur_is_idempotent(self) -> None:
        result = _run(self.script, "durdur")
        self.assertEqual(result.returncode, 0)
        self.assertIn("zaten kapali", result.stdout)

    def test_baslat_kills_foreign_motor_processes(self) -> None:
        # Ayni makinede ikinci bir motor Telegram'da 409 cakismasi yaratiyor:
        # baslat once eski surecleri kapatmali.
        marker = f"sqe_sahte_motor_{os.getpid()}"
        # `; true` sayesinde bash exec optimizasyonu yapmaz, isaretci komut
        # satirinda kalir ve pgrep -f bulabilir.
        fake = subprocess.Popen(["bash", "-c", "sleep 120; true", marker])
        self.addCleanup(fake.kill)

        env = dict(os.environ, MOTOR_SUREC_DESENI=marker, MOTOR_KILL_TIMEOUT="5")
        subprocess.run(
            ["bash", str(self.script), "baslat"],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

        fake.wait(timeout=15)

    def test_unknown_command_reports_usage(self) -> None:
        result = _run(self.script, "havaya-ucur")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Kullanim", result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

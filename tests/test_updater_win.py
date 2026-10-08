# Сторож обновления Windows (updater.WATCH_WIN) — настоящий PowerShell на подставных файлах: «установщик» — .cmd,
# который кончает с нужным кодом и пишет, с какими параметрами его запустили; «VastGame» — .cmd, который отмечает,
# что его открыли. Только в Windows (на Mac пропускается); остальное про обновление — tests/test_updater.py.

import base64
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "только Windows")
class TestWatchWindows(unittest.TestCase):
    def run_watch(self, code, old_pid=None):
        d = Path(tempfile.mkdtemp())
        folder = d / "update"
        folder.mkdir()
        setup, exe = folder / "VastGame-Setup-9.9.9.exe.cmd", d / "VastGame.cmd"
        setup.write_text(f'@echo %* > "{d}\\setup-args"\n@exit /b {code}\n')
        exe.write_text(f'@echo opened > "{d}\\opened"\n')
        pid = old_pid or subprocess.run(["cmd", "/c", "exit"]).returncode or 999999    # прежнее окно уже закрыто
        env = dict(os.environ, VG_FOLDER=str(folder), VG_PID=str(pid), VG_SETUP=str(setup), VG_VERSION="9.9.9",
                   VG_EXE=str(exe))
        t0 = time.time()
        # Ровно как запускает программа (updater._install_win): с теми же флагами — без консоли PowerShell молчал
        p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-EncodedCommand", base64.b64encode(updater.WATCH_WIN.encode("utf-16-le")).decode()],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **updater.watch_flags())
        p.wait(120)
        for _ in range(50):                      # «VastGame» запущен без ожидания — дать ему отметиться
            if (d / "opened").exists():
                break
            time.sleep(0.2)
        return d, folder, setup, time.time() - t0

    def test_installed(self):
        d, folder, setup, _ = self.run_watch(0)
        args = (d / "setup-args").read_text()
        for a in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG="):
            self.assertIn(a, args)
        self.assertTrue((d / "opened").exists())                       # открыли VastGame
        self.assertFalse(setup.exists())                               # установщик убран
        self.assertFalse((folder / "failed").exists())
        self.assertIn("поставлена 9.9.9", (folder / "update.log").read_text(encoding="utf-8-sig"))

    def test_setup_failed(self):
        d, folder, setup, _ = self.run_watch(5)
        self.assertTrue((d / "opened").exists())                       # прежнюю версию всё равно открыли
        self.assertEqual((folder / "failed").read_text(encoding="utf-8-sig").strip(), "9.9.9")
        self.assertTrue(setup.exists())
        self.assertIn("код 5", (folder / "update.log").read_text(encoding="utf-8-sig"))

    def test_waits_for_the_old_window(self):
        old = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", "Start-Sleep 3"])
        d, folder, setup, took = self.run_watch(0, old_pid=old.pid)
        old.wait()
        self.assertGreaterEqual(took, 2)                                # установщик — только после выхода окна
        self.assertTrue((d / "opened").exists())


if __name__ == "__main__":
    unittest.main()

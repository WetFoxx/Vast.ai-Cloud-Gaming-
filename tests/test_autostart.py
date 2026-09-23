# autostart: файл службы macOS пишется во временную папку, launchctl — подделка (ничего не ставится).

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import autostart  # noqa: E402


class TestAutostart(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for name, value in [("AGENTS_DIR", Path(tempfile.mkdtemp()) / "LaunchAgents"),
                            ("_launchctl", lambda *a: self.calls.append(a) or mock.Mock(returncode=0)),
                            ("sys", mock.Mock(platform="darwin", executable="/app/python"))]:
            p = mock.patch.object(autostart, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_install_and_status(self):
        self.assertEqual(autostart.status(), "off")
        self.assertTrue(autostart.install())
        d = plistlib.loads(autostart.plist_path("resume").read_bytes())
        self.assertEqual(d["ProgramArguments"][0], "/app/python")
        self.assertEqual(d["ProgramArguments"][2], "resume")
        self.assertTrue(d["RunAtLoad"] and d["AbandonProcessGroup"])
        self.assertEqual(self.calls[-1][0], "bootstrap")
        self.assertEqual(autostart.status(), "on")

    def test_refresh_only_rewrites_outdated(self):
        autostart.refresh()
        self.assertFalse(autostart.plist_path("resume").exists())      # выключенную не включаем сами
        autostart.install()
        d = plistlib.loads(autostart.plist_path("resume").read_bytes())
        d["ProgramArguments"][0] = "/old/python"         # проект переехал
        autostart.plist_path("resume").write_bytes(plistlib.dumps(d))
        self.assertEqual(autostart.status(), "outdated")
        autostart.refresh()
        self.assertEqual(autostart.status(), "on")

    def test_uninstall(self):
        autostart.install()
        autostart.uninstall()
        self.assertEqual(autostart.status(), "off")
        self.assertEqual(self.calls[-1][0], "bootout")

    def test_open_at_login_off_by_default_and_not_started_now(self):
        self.assertEqual(autostart.status("app"), "off")          # по умолчанию выключено
        self.assertTrue(autostart.install("app"))
        d = plistlib.loads(autostart.plist_path("app").read_bytes())
        self.assertEqual(d["ProgramArguments"][1:], [str(autostart.HERE / "vastgame.py"), "app"])
        self.assertNotIn("StartInterval", d)                       # только при входе, не каждые 5 минут
        self.assertNotIn("bootstrap", [c[0] for c in self.calls])  # второе окно прямо сейчас не открыли
        self.assertEqual(autostart.status("resume"), "off")        # другую службу не тронули


if __name__ == "__main__":
    unittest.main()

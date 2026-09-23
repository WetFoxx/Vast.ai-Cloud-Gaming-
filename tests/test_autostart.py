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
        for name, value in [("PLIST", Path(tempfile.mkdtemp()) / "LaunchAgents" / "x.plist"),
                            ("_launchctl", lambda *a: self.calls.append(a) or mock.Mock(returncode=0)),
                            ("sys", mock.Mock(platform="darwin", executable="/app/python"))]:
            p = mock.patch.object(autostart, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_install_and_status(self):
        self.assertEqual(autostart.status(), "off")
        self.assertTrue(autostart.install())
        d = plistlib.loads(autostart.PLIST.read_bytes())
        self.assertEqual(d["ProgramArguments"][0], "/app/python")
        self.assertEqual(d["ProgramArguments"][2], "resume")
        self.assertTrue(d["RunAtLoad"] and d["AbandonProcessGroup"])
        self.assertEqual(self.calls[-1][0], "bootstrap")
        self.assertEqual(autostart.status(), "on")

    def test_refresh_only_rewrites_outdated(self):
        autostart.refresh()
        self.assertFalse(autostart.PLIST.exists())      # выключенную не включаем сами
        autostart.install()
        d = plistlib.loads(autostart.PLIST.read_bytes())
        d["ProgramArguments"][0] = "/old/python"         # проект переехал
        autostart.PLIST.write_bytes(plistlib.dumps(d))
        self.assertEqual(autostart.status(), "outdated")
        autostart.refresh()
        self.assertEqual(autostart.status(), "on")

    def test_uninstall(self):
        autostart.install()
        autostart.uninstall()
        self.assertEqual(autostart.status(), "off")
        self.assertEqual(self.calls[-1][0], "bootout")


if __name__ == "__main__":
    unittest.main()

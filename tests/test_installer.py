# installer: ставим только официальные файлы с подписью нужного разработчика. Без сети: всё подменено.

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import installer  # noqa: E402
from vastapi import VastgameError  # noqa: E402

GOOD_PKG = """Package "ts.pkg":
   Status: signed by a developer certificate issued by Apple for distribution
   Notarization: trusted by the Apple notary service
   Certificate Chain:
    1. Developer ID Installer: Tailscale Inc. (W5364U7YZB)
"""


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class TestTailscale(unittest.TestCase):
    def setUp(self):
        self.cmds = []
        p = mock.patch.object(installer, "_download", lambda url, dest: Path(dest).write_bytes(b"x"))
        p.start()
        self.addCleanup(p.stop)

    def fake_run(self, pkgutil_out, osa=done()):
        def run(cmd, timeout, env=None):
            self.cmds.append(cmd[0])
            return done(out=pkgutil_out) if cmd[0] == "pkgutil" else osa
        return mock.patch.object(installer.paths, "run", run)

    def test_good_signature_installs_with_macos_password_window(self):
        with self.fake_run(GOOD_PKG):
            self.assertTrue(installer.tailscale())
        self.assertEqual(self.cmds, ["pkgutil", "osascript"])

    def test_foreign_signature_is_refused_before_install(self):
        with self.fake_run(GOOD_PKG.replace("Tailscale Inc. (W5364U7YZB)", "Evil Ltd (ABCDE12345)")):
            with self.assertRaises(VastgameError) as e:
                installer.tailscale()
        self.assertEqual(e.exception.key, "install.bad_signature")
        self.assertEqual(self.cmds, ["pkgutil"])                # до установки не дошло

    def test_unsigned_is_refused(self):
        with self.fake_run("Package: no signature"):
            with self.assertRaises(VastgameError):
                installer.tailscale()
        self.assertNotIn("osascript", self.cmds)

    def test_cancel_in_password_window(self):
        with self.fake_run(GOOD_PKG, osa=done(1, err="execution error: User canceled. (-128)")):
            with self.assertRaises(VastgameError) as e:
                installer.tailscale()
        self.assertEqual(e.exception.key, "install.cancelled")


class TestSteamLink(unittest.TestCase):
    def test_mac_opens_the_app_store_when_missing(self):
        cmds = []

        def run(cmd, timeout, env=None):
            cmds.append(cmd)
            return done()
        with mock.patch.object(installer.sys, "platform", "darwin"), \
             mock.patch.object(installer.moonlight_app, "steamlink_path", lambda: None), \
             mock.patch.object(installer.paths, "run", run):
            self.assertTrue(installer.steamlink())
        self.assertEqual(cmds, [["open", installer.STEAMLINK_STORE]])

    def test_already_installed_is_left_alone(self):
        with mock.patch.object(installer.moonlight_app, "steamlink_path", lambda: "/Applications/Steam Link.app"), \
             mock.patch.object(installer, "_download", lambda *_a: self.fail("качать не нужно")):
            self.assertTrue(installer.steamlink())

    def test_windows_refuses_a_foreign_signature(self):
        import zipfile

        def download(_url, dest):
            with zipfile.ZipFile(dest, "w") as z:
                z.writestr("SteamLink.msi", b"not a real msi")
        ran = []

        def run(cmd, timeout, env=None):
            ran.append(env.get("VG_FILE", "") if env else "")
            return done(out="CN=Evil, O=Evil Corp, L=X")
        with mock.patch.object(installer.sys, "platform", "win32"), \
             mock.patch.object(installer.moonlight_app, "steamlink_path", lambda: None), \
             mock.patch.object(installer, "_download", download), \
             mock.patch.object(installer.paths, "run", run):
            with self.assertRaises(VastgameError) as e:
                installer.steamlink()
        self.assertEqual(e.exception.key, "install.bad_signature")
        self.assertEqual(len(ran), 1)          # только проверка подписи, msiexec не запускался


if __name__ == "__main__":
    unittest.main()

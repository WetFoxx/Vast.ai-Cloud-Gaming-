# drive.client: свой клиент из Связки ключей важнее; без него — клиент приложения из oauth_client.json.

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drive  # noqa: E402


class TestClient(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "oauth_client.json"
        self.tmp.write_text(json.dumps({"client_id": "app.apps.googleusercontent.com", "client_secret": "S"}))
        p = mock.patch.object(drive, "BUNDLED_CLIENT", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def test_bundled_when_keychain_empty(self):
        with mock.patch.object(drive.keystore, "get", lambda k: None):
            self.assertEqual(drive.client(), ("app.apps.googleusercontent.com", "S"))

    def test_own_client_wins(self):
        own = {"GOOGLE_CLIENT_ID": "mine.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "M"}
        with mock.patch.object(drive.keystore, "get", lambda k: own.get(k)):
            self.assertEqual(drive.client(), ("mine.apps.googleusercontent.com", "M"))

    def test_nothing(self):
        self.tmp.unlink()
        with mock.patch.object(drive.keystore, "get", lambda k: None):
            self.assertEqual(drive.client(), (None, None))


class TestTsLoginLink(unittest.TestCase):
    def link(self, text, code=0):
        import subprocess
        with mock.patch.object(drive, "rclone", lambda *a, **k: subprocess.CompletedProcess(a, code, text, "")):
            return drive.ts_login_link(42)

    def test_link_of_this_machine(self):
        self.assertEqual(self.link("42\nhttps://login.tailscale.com/a/abc\n"), "https://login.tailscale.com/a/abc")

    def test_other_machine_or_garbage_is_ignored(self):
        self.assertIsNone(self.link("41\nhttps://login.tailscale.com/a/abc\n"))   # прошлая машина
        self.assertIsNone(self.link("42\nhttps://evil.example/a/abc\n"))         # не Tailscale
        self.assertIsNone(self.link("", code=3))                                   # файла нет


if __name__ == "__main__":
    unittest.main()

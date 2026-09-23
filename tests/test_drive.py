# drive.client: свой клиент из Keychain важнее; без него — клиент приложения из oauth_client.json.

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
        with mock.patch.object(drive.keyring, "get_password", lambda s, k: None):
            self.assertEqual(drive.client(), ("app.apps.googleusercontent.com", "S"))

    def test_own_client_wins(self):
        own = {"GOOGLE_CLIENT_ID": "mine.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "M"}
        with mock.patch.object(drive.keyring, "get_password", lambda s, k: own.get(k)):
            self.assertEqual(drive.client(), ("mine.apps.googleusercontent.com", "M"))

    def test_nothing(self):
        self.tmp.unlink()
        with mock.patch.object(drive.keyring, "get_password", lambda s, k: None):
            self.assertEqual(drive.client(), (None, None))


if __name__ == "__main__":
    unittest.main()

# sshkey: без SSH-ключа в аккаунте Vast не запускает VM — vastgame добавляет свой сам.
# Vast подделан (сети нет); ключ создаёт настоящий ssh-keygen во временной папке.

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core     # noqa: E402
import sshkey   # noqa: E402
from vastapi import VastgameError  # noqa: E402


class FakeVast:
    def __init__(self, keys):
        self.keys, self.posted = keys, []

    def __call__(self, method, path, body=None, **kw):
        if (method, path) == ("GET", "ssh/"):
            return self.keys
        if (method, path) == ("POST", "ssh/"):
            self.posted.append(body["ssh_key"])
            return {"success": True}
        raise AssertionError(f"неожиданный запрос {method} {path}")


class TestEnsure(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        for name, value in (("KEY", d / "vast_ed25519"), ("PUB", d / "vast_ed25519.pub")):
            p = mock.patch.object(sshkey, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, vast):
        with mock.patch.object(sshkey, "call", vast):
            return sshkey.ensure()

    def test_key_in_account_nothing_to_do(self):
        vast = FakeVast([{"id": 1, "public_key": "ssh-ed25519 AAA", "deleted_at": None}])
        self.assertFalse(self.run_with(vast))
        self.assertEqual(vast.posted, [])
        self.assertFalse(sshkey.KEY.exists())          # ключ даже не создавали

    def test_no_key_creates_and_sends_only_public(self):
        vast = FakeVast([{"id": 1, "public_key": "ssh-rsa old", "deleted_at": "2026-01-01"}])  # удалённый не считается
        self.assertTrue(self.run_with(vast))
        self.assertEqual(len(vast.posted), 1)
        self.assertTrue(vast.posted[0].startswith("ssh-ed25519 "))
        self.assertNotIn("PRIVATE", vast.posted[0])    # в Vast уходит только открытая часть
        self.assertIn("PRIVATE KEY", sshkey.KEY.read_text())   # секретная — осталась здесь

    def test_same_key_reused(self):
        self.run_with(FakeVast([]))
        first = sshkey.PUB.read_text()
        vast = FakeVast([])                             # человек удалил ключ на сайте — отправляем тот же
        self.run_with(vast)
        self.assertEqual(vast.posted, [first.strip()])

    def test_rejected(self):
        vast = FakeVast([])
        vast.__class__ = type("Rejecting", (FakeVast,), {
            "__call__": lambda self, m, p, b=None, **k: [] if m == "GET" else {"success": False, "msg": "bad key"}})
        with self.assertRaises(VastgameError) as e:
            self.run_with(vast)
        self.assertEqual(e.exception.key, "sshkey.rejected")


class TestRent(unittest.TestCase):
    def rent(self, ensure):
        calls = []
        with mock.patch.object(core.sshkey, "ensure", ensure), \
             mock.patch.object(core, "call", lambda m, p, b=None: calls.append((m, p)) or {"success": True, "new_contract": 7}), \
             mock.patch.object(core.history, "started", lambda *a: None):
            iid, _ = core.rent(1, {})
        return iid, calls

    def test_key_checked_before_renting(self):
        order = []
        iid, calls = self.rent(lambda: order.append("ssh") or True)
        self.assertEqual((iid, order, calls), (7, ["ssh"], [("PUT", "asks/1/")]))

    def test_key_check_failure_does_not_block_rent(self):
        def broken():
            raise VastgameError("нет связи", key="vast.network")
        iid, calls = self.rent(broken)
        self.assertEqual((iid, calls), (7, [("PUT", "asks/1/")]))


if __name__ == "__main__":
    unittest.main()

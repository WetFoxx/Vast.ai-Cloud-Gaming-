# pairing: автоматическая связка Moonlight — без сети, настоящего Moonlight и Связки ключей.

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pairing  # noqa: E402
from vastapi import VastgameError  # noqa: E402


class FakeResponse:
    def __init__(self, code, status=None):
        self.status_code, self._status = code, status

    def json(self):
        return {"status": self._status}


class TestPair(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        # Поддельный Moonlight: «pair … --pin N» ждёт, пока Sunshine примет PIN (файл-флажок), и выходит с 0
        exe = self.d / "Moonlight"
        exe.write_text(f"#!/bin/sh\necho \"$@\" > {self.d}/args\nfor i in $(seq 1 50); do [ -e {self.d}/ok ] && exit 0; sleep 0.1; done\nexit 1\n")
        exe.chmod(0o755)
        self.ssh, self.posts, self.order = [], [], []
        p = mock.patch.object(pairing.requests, "get", self.fake_get)
        p.start()
        self.addCleanup(p.stop)
        for obj, name, value in [(pairing.moonlight, "path", lambda: str(exe)),
                                 (pairing, "creds", lambda: ("vastgame", "secret")),
                                 (pairing, "ssh_run", self.fake_ssh),
                                 (pairing, "_later", lambda fn: fn()),   # фоновую выгрузку — сразу, для проверки
                                 (pairing.time, "sleep", lambda s: None)]:
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def fake_ssh(self, ip, command, timeout=180, on_notice=None, stdin_text=None):
        self.ssh.append((command, stdin_text))
        if command.startswith("wolf sunshine-creds"):
            self.order.append("set-login")
            self.creds_ok = True
            return 0, "sunshine-creds: ok\n"
        return 0, ""

    def fake_get(self, url, auth, verify, timeout):
        self.order.append("check-login")
        return FakeResponse(200 if getattr(self, "creds_ok", False) else 401)

    def fake_post(self, url, json, auth, verify, timeout):
        self.posts.append(json["pin"])
        self.order.append("pin")
        if not getattr(self, "creds_ok", False):
            return FakeResponse(401)
        (self.d / "ok").touch()
        return FakeResponse(200, True)

    def test_pairs_and_sets_own_login_when_needed(self):
        with mock.patch.object(pairing.requests, "post", self.fake_post):
            self.assertTrue(pairing.pair("vastai-gaming-6.tail.ts.net", "100.1.1.1"))
        args = (self.d / "args").read_text().split()
        self.assertEqual(args[:2], ["pair", "vastai-gaming-6.tail.ts.net"])
        pin = args[args.index("--pin") + 1]
        self.assertTrue(pin.isdigit() and len(pin) == 4)
        self.assertEqual(set(self.posts), {pin})                       # Sunshine получил тот же PIN
        creds_calls = [c for c in self.ssh if c[0].startswith("wolf sunshine-creds")]
        self.assertEqual(creds_calls, [("wolf sunshine-creds vastgame", "secret\n")])   # пароль — во вход, не в команду
        self.assertTrue(any("push identity" in c[0] for c in self.ssh))  # связка — сразу в облако
        # Логин задан ДО первого PIN: перезапуск Sunshine не обрывает связку
        self.assertLess(self.order.index("set-login"), self.order.index("pin"))
        self.assertTrue(self.posts)

    def test_our_login_already_works(self):
        self.creds_ok = True
        with mock.patch.object(pairing.requests, "post", self.fake_post):
            pairing.pair("h", "100.1.1.1")
        self.assertFalse(any(c[0].startswith("wolf sunshine-creds") for c in self.ssh))

    def test_moonlight_fails(self):
        self.creds_ok = True

        def post(*a, **k):
            return FakeResponse(200, False)              # Sunshine не принял — Moonlight так и не связался
        with mock.patch.object(pairing.requests, "post", post), mock.patch.object(pairing, "PAIR_WAIT", 1):
            with self.assertRaises(VastgameError) as e:
                pairing.pair("h", "100.1.1.1")
        self.assertIn(e.exception.key, ("pair.timeout", "pair.failed"))


if __name__ == "__main__":
    unittest.main()

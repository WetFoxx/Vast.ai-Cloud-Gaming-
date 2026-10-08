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



def _no_page():
    """Страница wolf (v3.15) в этих тестах «не отвечает» — проверяется путь по SSH (старые машины)."""
    p = mock.patch.object(pairing.wolfnode, "post_cmd", lambda *a, **k: None)
    p.start()
    return p


def setUpModule():
    unittest.addModuleCleanup(_no_page().stop)
    p = mock.patch.object(pairing, "applog", LOG.append)      # app.log — в память, не в папку настроек
    p.start()
    unittest.addModuleCleanup(p.stop)


LOG = []

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

    def fake_get(self, url, auth, verify, timeout, **kw):
        self.order.append("check-login")
        return FakeResponse(200 if getattr(self, "creds_ok", False) else 401)

    def fake_post(self, url, json, auth, verify, timeout, **kw):
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

    def _hanging_moonlight(self):
        """Moonlight, который после связки не выходит (Windows: окно с итогом, которого никто не видит)."""
        if type(self) is not TestPair:
            self.skipTest("достаточно базовой поддельной Sunshine")
        exe = self.d / "Moonlight"
        exe.write_text(f"#!/bin/sh\necho \"$@\" > {self.d}/args\nsleep 30\n")
        exe.chmod(0o755)

    def test_moonlight_does_not_exit_but_is_paired(self):
        self.creds_ok = True
        self._hanging_moonlight()
        with mock.patch.object(pairing.requests, "post", self.fake_post), mock.patch.object(pairing, "PAIR_SETTLE", 0), \
                mock.patch.object(pairing.moonlight, "apps", lambda host: ["Desktop"]):
            self.assertTrue(pairing.pair("h", "100.1.1.1"))

    def test_moonlight_does_not_exit_and_is_not_paired(self):
        self.creds_ok = True
        self._hanging_moonlight()
        with mock.patch.object(pairing.requests, "post", self.fake_post), mock.patch.object(pairing, "PAIR_SETTLE", 0), \
                mock.patch.object(pairing.moonlight, "apps", lambda host: None):
            with self.assertRaises(VastgameError) as e:
                pairing.pair("h", "100.1.1.1")
        self.assertEqual(e.exception.key, "pair.failed")

    def test_moonlight_fails(self):
        self.creds_ok = True

        def post(*a, **k):
            return FakeResponse(200, False)              # Sunshine не принял — Moonlight так и не связался
        with mock.patch.object(pairing.requests, "post", post), mock.patch.object(pairing, "PAIR_WAIT", 1):
            with self.assertRaises(VastgameError) as e:
                pairing.pair("h", "100.1.1.1")
        self.assertIn(e.exception.key, ("pair.timeout", "pair.failed"))


class FakeJSON:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


class TestPairNewSunshine(TestPair):
    """Sunshine с 2026.906: PIN — только с номером связки (GET /api/pin); прошлая незаконченная попытка
    висит 5 минут и блокирует новую с того же Mac (409), поэтому её надо отменить до «Moonlight pair»."""
    STALE, NEW = "a" * 32, "b" * 32

    def setUp(self):
        super().setUp()
        self.creds_ok = True
        self.pending = [self.STALE]                       # старая попытка с этого Mac ещё не истекла
        self.deleted, self.bodies = [], []

    def moonlight_started(self):
        return (self.d / "args").exists()

    def fake_get(self, url, auth, verify, timeout, **kw):
        if url.endswith("/api/pin"):
            if self.moonlight_started() and self.NEW not in self.pending and not (self.d / "ok").exists():
                if self.STALE in self.pending:                           # как настоящая: 409, Moonlight ждёт зря
                    return FakeJSON(200, {"pairings": [{"id": i} for i in self.pending]})
                self.pending.append(self.NEW)                            # наш Moonlight постучался
            return FakeJSON(200, {"pairings": [{"id": i, "name": "roth", "address": "100.64.0.2"}
                                               for i in self.pending]})
        return FakeJSON(200, {"status": True})                           # /api/config — логин подходит

    def fake_delete(self, url, json, auth, verify, timeout, **kw):
        self.deleted.append((json["pairing_id"], self.moonlight_started()))
        self.pending.remove(json["pairing_id"])
        return FakeJSON(200, {"status": True})

    def fake_post(self, url, json, auth, verify, timeout, **kw):
        self.bodies.append(dict(json))
        if "pairing_id" not in json:
            return FakeJSON(400, {"status": False, "error": "pairing_id must contain exactly 32 hexadecimal characters"})
        if json["pairing_id"] == self.NEW and self.NEW in self.pending:
            self.pending.remove(self.NEW)
            (self.d / "ok").touch()
            return FakeJSON(200, {"status": True})
        return FakeJSON(200, {"status": False})

    def test_pairs_with_pairing_id(self):
        with mock.patch.object(pairing.requests, "post", self.fake_post), \
             mock.patch.object(pairing.requests, "delete", self.fake_delete):
            self.assertTrue(pairing.pair("h", "100.1.1.1"))
        args = (self.d / "args").read_text().split()
        pin = args[args.index("--pin") + 1]
        self.assertEqual(self.deleted, [(self.STALE, False)])            # старая отменена ДО Moonlight
        self.assertEqual(self.bodies[-1], {"pin": pin, "name": "vastgame", "pairing_id": self.NEW})
        self.assertTrue(all(b.get("pairing_id") != self.STALE for b in self.bodies))

    # Прежние тесты — про старую Sunshine; здесь они не нужны
    test_pairs_and_sets_own_login_when_needed = test_our_login_already_works = test_moonlight_fails = None


class TestPairFreshSunshine(TestPairNewSunshine):
    """Новый человек: облако пустое (нет identity) → у Sunshine логин кабинета ещё не задан. Такая Sunshine на ЛЮБОЙ
    запрос отвечает 307 → /welcome. Если идти по переадресации, там обычная страница (200) — логин будто подходит,
    его не задают, PIN не принимается, связка висит полторы минуты (у двух друзей, 2026-09-26/27)."""

    def setUp(self):
        super().setUp()
        self.creds_ok = False
        self.pending = []

    def welcome(self, kw, method="GET"):
        """Ответ Sunshine без логина. requests по умолчанию сам переходит по 307 — и получает страницу /welcome
        (GET → 200) или «плохой запрос» (POST на /welcome → 400)."""
        if kw.get("allow_redirects", True):
            return FakeJSON(200, None) if method == "GET" else FakeJSON(400, {"status": False})
        return FakeJSON(307, None)

    def fake_get(self, url, auth, verify, timeout, **kw):
        if not self.creds_ok:
            return self.welcome(kw)
        return super().fake_get(url, auth, verify, timeout)

    def fake_post(self, url, json, auth, verify, timeout, **kw):
        if not self.creds_ok:
            self.bodies.append(dict(json))
            return self.welcome(kw, "POST")
        return super().fake_post(url, json, auth, verify, timeout)

    def fake_delete(self, url, json, auth, verify, timeout, **kw):
        if not self.creds_ok:
            return self.welcome(kw, "DELETE")
        return super().fake_delete(url, json, auth, verify, timeout)

    def test_pairs_with_pairing_id(self):
        LOG.clear()
        with mock.patch.object(pairing.requests, "post", self.fake_post), \
             mock.patch.object(pairing.requests, "delete", self.fake_delete), \
             mock.patch.object(pairing, "PAIR_WAIT", 5):
            self.assertTrue(pairing.pair("h", "100.1.1.1"))
        creds_calls = [c for c in self.ssh if c[0].startswith("wolf sunshine-creds")]
        self.assertEqual(creds_calls, [("wolf sunshine-creds vastgame", "secret\n")])   # логин задан
        self.assertTrue(any("логина кабинета нет" in x for x in LOG), LOG)             # и это видно в app.log
        self.assertTrue(any(x.startswith("moonlight: связано за") for x in LOG), LOG)
        self.assertFalse(any("secret" in x for x in LOG))                               # пароля в журнале нет


if __name__ == "__main__":
    unittest.main()


class TestCreds(unittest.TestCase):
    """Пароль кабинета — из ключа Vast: один и тот же на всех компьютерах человека (Mac и Windows)."""

    def creds_with(self, store):
        puts = []
        with mock.patch.object(pairing.keystore, "get", lambda name: store.get(name)), \
                mock.patch.object(pairing.keystore, "put", lambda name, value: puts.append(name)):
            return pairing.creds(), puts

    def test_same_vast_key_same_password_everywhere(self):
        a, puts = self.creds_with({"VAST_API_KEY": "key-1", "SUNSHINE_PASSWORD": "old-mac"})
        b, _ = self.creds_with({"VAST_API_KEY": "key-1"})                  # другой компьютер, своего пароля нет
        self.assertEqual(a, b)
        self.assertEqual((a[0], len(a[1])), ("vastgame", 24))
        self.assertNotIn("key-1", a[1])
        self.assertEqual(puts, [])                                         # ничего не сохраняем
        self.assertNotEqual(self.creds_with({"VAST_API_KEY": "key-2"})[0], a)

    def test_without_vast_key_random_and_saved(self):
        (user, pw), puts = self.creds_with({})
        self.assertEqual((user, puts), ("vastgame", ["SUNSHINE_PASSWORD"]))
        self.assertGreaterEqual(len(pw), 20)

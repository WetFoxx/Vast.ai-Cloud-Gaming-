# updater: сравнение версий, проверка «есть ли новее» у поддельного GitHub (настоящий HTTP-сервер на
# 127.0.0.1 — ответы, мусор, огромный файл, «выпусков нет», нет связи), проверка в фоне для окна.

import http.server
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater  # noqa: E402

ANSWERS = {                                   # путь → (код, тело)
    "/newer": (200, json.dumps({"version": "9.9.9", "file": "vastgame-9.9.9.zip"})),
    "/same": (200, json.dumps({"version": updater.VERSION})),
    "/junk": (200, "<html>не JSON</html>"),
    "/not-version": (200, json.dumps({"version": "1.0.0/../../evil"})),
    "/list": (200, json.dumps(["9.9.9"])),
    "/huge": (200, json.dumps({"version": "9.9.9", "pad": "x" * (updater.MAX_BYTES + 10)})),
    "/redirect": (302, ""),
}


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code, body = ANSWERS.get(self.path, (404, "Not Found"))
        self.send_response(code)
        if code == 302:                          # как GitHub: «последний выпуск» → файл конкретного выпуска
            self.send_header("Location", "/newer")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def setUpModule():
    global BASE, SERVER
    SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"


def tearDownModule():
    SERVER.shutdown()
    SERVER.server_close()


class TestVersions(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(updater.newer("0.2.10", "0.2.9"))         # числа, а не буквы
        self.assertTrue(updater.newer("1.0.0", "0.9.99"))
        self.assertFalse(updater.newer("0.2.0", "0.2.0"))
        self.assertFalse(updater.newer("0.1.9", "0.2.0"))
        for bad in (None, "", "1.0", "1.0.0.0", "v1.0.0", "1.0.0-beta", "1.0.0/../evil", 100):
            self.assertIsNone(updater.parse(bad))
            self.assertFalse(updater.newer(bad, "0.0.1"))

    def test_page_built_from_number(self):
        self.assertEqual(updater.page("0.2.1"),
                         "https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/tag/vastgame-v0.2.1")

    def test_own_version_is_a_version(self):
        self.assertIsNotNone(updater.parse(updater.VERSION))


class TestLatest(unittest.TestCase):
    def test_answers(self):
        self.assertEqual(updater.latest(BASE + "/newer"), "9.9.9")
        self.assertEqual(updater.latest(BASE + "/redirect"), "9.9.9")    # по переадресации, как у GitHub
        self.assertEqual(updater.latest(BASE + "/same"), updater.VERSION)
        for path in ("/junk", "/not-version", "/list", "/huge", "/no-releases-yet"):
            self.assertIsNone(updater.latest(BASE + path), path)

    def test_no_connection(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                  # порт свободен — соединение отвергнут
        self.assertIsNone(updater.latest(f"http://127.0.0.1:{port}/newer"))

    def test_timeout(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)                                # принимает соединение, но молчит
        try:
            with mock.patch.object(updater, "TIMEOUT", 0.5):
                self.assertIsNone(updater.latest(f"http://127.0.0.1:{s.getsockname()[1]}/newer"))
        finally:
            s.close()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestChecker(unittest.TestCase):
    def checker(self, answers):
        """Проверка без фона: fetch отвечает по очереди из answers; clock — поддельные часы."""
        self.clock, self.calls = Clock(), []

        def fetch():
            self.calls.append(self.clock.now)
            return answers.pop(0)
        return updater.Checker(fetch=fetch, clock=self.clock, spawn=lambda f: f())

    def test_newer_version_is_offered(self):
        c = self.checker(["9.9.9"])
        s = c.status()
        self.assertEqual(s, {"current": updater.VERSION, "available": True, "latest": "9.9.9",
                             "page": updater.page("9.9.9")})

    def test_same_or_older_is_not_offered(self):
        for v in (updater.VERSION, "0.0.1", None):
            s = self.checker([v]).status()
            self.assertEqual((s["available"], s["latest"], s["page"]), (False, None, None))

    def test_checks_rarely(self):
        c = self.checker(["9.9.9", "9.9.10"])
        c.status()
        self.clock.now += updater.CHECK_EVERY - 1
        self.assertEqual(c.status()["latest"], "9.9.9")        # рано — сеть не трогаем
        self.clock.now += 1
        self.assertEqual(c.status()["latest"], "9.9.10")
        self.assertEqual(len(self.calls), 2)

    def test_failure_retries_sooner_and_keeps_what_was_known(self):
        c = self.checker(["9.9.9", None, "9.9.9"])
        c.status()
        self.clock.now += updater.CHECK_EVERY
        self.assertTrue(c.status()["available"])               # нет сети — показываем, что уже знали
        self.clock.now += updater.RETRY_AFTER
        c.status()
        self.assertEqual(len(self.calls), 3)

    def test_one_check_at_a_time(self):
        # Пока проверка идёт в фоне, новые вопросы окна не запускают вторую
        started = []
        c = updater.Checker(fetch=lambda: "9.9.9", spawn=started.append)
        c.status()
        c.status()
        self.assertEqual(len(started), 1)
        started[0]()                                           # фоновая проверка закончилась
        self.assertEqual(c.status()["latest"], "9.9.9")
        self.assertEqual(len(started), 1)                      # следующая — через CHECK_EVERY


if __name__ == "__main__":
    unittest.main()

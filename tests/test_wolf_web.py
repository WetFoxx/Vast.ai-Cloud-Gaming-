# Страница wolf на машине (wolf-setup.sh v3.15): команды от приложения вместо Tailscale SSH.
# Берём настоящий код wolf-web.py из wolf-setup.sh и запускаем здесь с поддельными tailscale и wolf;
# приложение (wolfnode.run) ходит к нему по-настоящему, по HTTP.

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wolfnode  # noqa: E402
from vastapi import VastgameError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER = "2132240893056654"


def load_wolf_web(fake_wolf):
    """Код wolf-web.py из heredoc в wolf-setup.sh — без последней строки (она сама запускает сервер)."""
    src = (ROOT / "wolf-setup.sh").read_text()
    code = re.search(r"^w /usr/local/bin/wolf-web.py <<'WEB'\n(.*?)^WEB$", src, re.S | re.M).group(1)
    code = code.replace("WOLF = '/usr/local/bin/wolf'", f"WOLF = {str(fake_wolf)!r}")
    code = "\n".join(ln for ln in code.splitlines() if not ln.startswith("ThreadingHTTPServer("))
    ns = {"__name__": "wolf_web"}
    exec(compile(code, "wolf-web.py", "exec"), ns)
    return ns


class TestWolfWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = cls.d = Path(tempfile.mkdtemp())
        # Поддельный tailscale: владелец машины OWNER; whois — владелец из FAKE_WHO (и теги из FAKE_TAGS)
        ts = d / "tailscale"
        # Как настоящий: --json — только сразу после подкоманды (проверено на Tailscale 1.102, 2026-09-25)
        ts.write_text("#!/bin/sh\n[ \"$2\" = --json ] || { echo 'too many arguments' >&2; exit 1; }\ncase \"$1\" in\n"
                      f"  status) echo '{{\"Self\": {{\"UserID\": {OWNER}}}}}';;\n"
                      "  whois) echo \"{\\\"UserProfile\\\": {\\\"ID\\\": $(cat " + str(d) + "/who)}, "
                      "\\\"Node\\\": {\\\"Tags\\\": $(cat " + str(d) + "/tags)}}\";;\nesac\n")
        # Поддельный wolf: записывает, что его попросили и что пришло на вход
        wolf = d / "wolf"
        wolf.write_text(f"#!/bin/sh\necho \"$@\" >> {d}/calls\ncat > {d}/stdin\necho \"wolf: $@\"\n"
                        "[ \"$1\" = finish ] && echo 'finish: запущено'\nexit 0\n")
        for f in (ts, wolf):
            f.chmod(0o755)
        cls.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{d}:{cls.old_path}"
        ns = load_wolf_web(wolf)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ns["H"])
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        os.environ["PATH"] = cls.old_path

    def setUp(self):
        self.owner(OWNER)
        for f in ("calls", "stdin"):
            (self.d / f).unlink(missing_ok=True)
        p = mock.patch.object(wolfnode, "WOLF_PORT", self.port)
        p.start()
        self.addCleanup(p.stop)
        self.ssh = []

    def owner(self, who, tags="null"):
        (self.d / "who").write_text(who)
        (self.d / "tags").write_text(tags)

    def run_cmd(self, *a, **k):
        return wolfnode.run("127.0.0.1", *a, ssh=lambda *x, **y: self.ssh.append(x) or (0, "по SSH"), **k)

    def calls(self):
        p = self.d / "calls"
        return p.read_text().splitlines() if p.exists() else []

    def test_same_owner_runs_commands_without_ssh(self):
        self.assertEqual(self.run_cmd("ping"), (0, "ok\n"))
        code, out = self.run_cmd("time")
        self.assertTrue(out.strip().isdigit())
        code, out = self.run_cmd("finish")
        self.assertIn("finish: запущено", out)
        self.assertEqual(self.calls(), ["finish"])
        self.assertEqual(self.ssh, [])                                  # SSH не понадобился

    def test_sunshine_password_goes_to_stdin_not_args(self):
        code, out = self.run_cmd("sunshine-creds", ["vastgame"], stdin="секрет\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), ["sunshine-creds vastgame"])
        self.assertEqual((self.d / "stdin").read_text(), "секрет\n")

    def test_forget_name_checked(self):
        for bad in ("../etc/passwd", "sgame--a/b", "-rf", "identity", "pfx--1\nfinish"):
            self.assertIsNone(wolfnode.post_cmd("127.0.0.1", "forget", [bad])[0], bad)
        self.assertEqual(self.calls(), [])
        # Настоящие имена бывают с пробелами (облако Алексея 2026-09-25)
        self.assertEqual(self.run_cmd("forget", ["sgame--The Blood of Dawnwalker"])[0], 0)
        self.assertEqual(self.run_cmd("forget", ["sgame--Proton - Experimental"])[0], 0)
        self.assertEqual(self.calls(), ["forget sgame--The Blood of Dawnwalker", "forget sgame--Proton - Experimental"])

    def test_login_strict(self):
        self.assertIsNone(wolfnode.post_cmd("127.0.0.1", "sunshine-creds", ["a b"], stdin="x")[0])
        self.assertEqual(self.calls(), [])

    def test_push_identity_in_background(self):
        self.assertEqual(self.run_cmd("push-identity"), (0, "started"))
        for _ in range(50):
            if self.calls():
                break
            time.sleep(0.05)
        self.assertEqual(self.calls(), ["push identity"])

    def test_unknown_command_refused(self):
        r = wolfnode.post_cmd("127.0.0.1", "rm -rf /")
        self.assertEqual(r[0], None)
        self.assertEqual(self.calls(), [])

    def test_other_owner_refused(self):
        self.owner("999")
        with self.assertRaises(VastgameError) as e:
            self.run_cmd("finish")
        self.assertEqual(e.exception.key, "machine.forbidden")
        self.assertEqual((self.calls(), self.ssh), ([], []))           # и по SSH в обход не пошли

    def test_tagged_device_refused(self):
        self.owner(OWNER, '["tag:server"]')
        with self.assertRaises(VastgameError):
            self.run_cmd("ping")

    def test_old_machine_falls_back_to_ssh(self):
        # Скрипт до v3.15: страница есть, но POST не умеет (501) — идём по SSH, как раньше
        with mock.patch.object(wolfnode.requests, "post", lambda *a, **k: mock.Mock(status_code=501)):
            self.assertEqual(self.run_cmd("forget", ["pfx--1"]), (0, "по SSH"))
        self.assertEqual(self.ssh, [("127.0.0.1", "wolf forget pfx--1")])


if __name__ == "__main__":
    unittest.main()

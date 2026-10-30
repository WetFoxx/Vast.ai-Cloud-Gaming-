# setupcheck.status: без ключа Vast остальное не проверяется и играть нельзя; warn не мешает играть.

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import setupcheck  # noqa: E402


def step(id_, state):
    return {"id": id_, "state": state, "key": "k", "params": {}}


class TestStatus(unittest.TestCase):
    def setUp(self):
        # Шаг службы автозапуска по умолчанию «выключено» — не зависеть от того, что стоит на этом Mac
        p = mock.patch.object(setupcheck.autostart, "status", lambda name="resume": "off")
        p.start()
        self.addCleanup(p.stop)
        self.keys = {}                                  # поддельная Связка ключей (настоящую тесты не трогают)
        p = mock.patch.object(setupcheck.keystore, "get", lambda name: self.keys.get(name))
        p.start()
        self.addCleanup(p.stop)

    def patch(self, **kw):
        for name, value in kw.items():
            p = mock.patch.object(setupcheck, name, value)
            p.start()
            self.addCleanup(p.stop)

    def slow(self, value, sec=0.4):
        def f(*a):
            time.sleep(sec)
            return value
        return f

    def test_checks_run_at_once(self):
        # [0.4.5] Ключ Vast есть — проверки аккаунта идут одновременно с проверкой ключа: время ≈ самая долгая
        self.keys["VAST_API_KEY"] = "k"
        self.patch(tools=self.slow([step("rclone", "ok")]), vast_key=self.slow(step("vast", "ok")),
                   _vast_secrets=self.slow(set()), ssh_key=self.slow(step("sshkey", "ok")),
                   google=lambda s: (time.sleep(0.4), step("google", "ok"), s())[1],
                   tailscale_key=lambda s: step("tailscale_key", "ok"), template=self.slow(step("template", "ok")))
        t0 = time.time()
        r = setupcheck.status()
        self.assertLess(time.time() - t0, 1.0)          # по очереди было бы ≥ 2.4 с
        self.assertEqual([s["id"] for s in r["steps"]],
                         ["rclone", "vast", "sshkey", "google", "tailscale_key", "template", "autostart", "open_at_login"])
        self.assertTrue(r["ready"])

    def test_bad_key_discards_account_checks(self):
        self.keys["VAST_API_KEY"] = "плохой"
        def broken():
            raise setupcheck.VastgameError("401", key="vast.http")
        self.patch(tools=lambda: [], vast_key=lambda: step("vast", "missing"), _vast_secrets=lambda: set(),
                   ssh_key=lambda: step("sshkey", "ok"), google=lambda s: step("google", "ok"),
                   tailscale_key=lambda s: step("tailscale_key", "ok"), template=broken)
        r = setupcheck.status()
        self.assertEqual([s["key"] for s in r["steps"][1:5]],
                         ["setup.sshkey.needs_vast", "setup.google.needs_vast", "setup.tailscale_key.needs_vast",
                          "setup.template.needs_vast"])

    def test_google_waits_for_secrets_only_at_the_end(self):
        with mock.patch.object(setupcheck.drive, "client", lambda: ("id", "secret")), \
             mock.patch.object(setupcheck.drive, "token", lambda: "tok"), \
             mock.patch.object(setupcheck.drive, "is_old_token", lambda: False), \
             mock.patch.object(setupcheck.drive, "check", lambda tok, sizes=True: ("a@b.c", 3, 1.5)):
            got = setupcheck.google(lambda: {"RCLONE_REFRESH_TOKEN", "RCLONE_CLIENT_ID", "RCLONE_CLIENT_SECRET"})
            self.assertEqual(got["state"], "ok")
            self.assertEqual(setupcheck.google(lambda: set())["key"], "setup.google.not_in_vast")

    def test_no_vast_key(self):
        self.patch(tools=lambda: [step("rclone", "ok")], vast_key=lambda: step("vast", "missing"))
        r = setupcheck.status()
        self.assertFalse(r["ready"])
        self.assertEqual([s["key"] for s in r["steps"][2:]],
                         ["setup.sshkey.needs_vast", "setup.google.needs_vast", "setup.tailscale_key.needs_vast", "setup.template.needs_vast",
                          "setup.autostart.off", "setup.open_at_login.off"])

    def test_warn_does_not_block(self):
        self.patch(tools=lambda: [step("rclone", "ok")], vast_key=lambda: step("vast", "warn"),
                   _vast_secrets=lambda: set(), ssh_key=lambda: step("sshkey", "ok"),
                   google=lambda s: step("google", "warn"),
                   tailscale_key=lambda s: step("tailscale_key", "ok"), template=lambda: step("template", "ok"))
        r = setupcheck.status()
        self.assertTrue(r["ready"])                     # служба выключена (warn) — играть всё равно можно
        self.assertEqual([s["id"] for s in r["steps"][-2:]], ["autostart", "open_at_login"])

    def test_fresh_asks_derp_again(self):
        seen = []
        self.patch(tools=lambda: [], vast_key=lambda: step("vast", "missing"),
                   derp=lambda fresh=False: seen.append(fresh) or None)
        setupcheck.status(fresh=True)
        self.assertEqual(seen, [True])

    def test_autostart_hidden_where_unsupported(self):
        self.patch(tools=lambda: [], vast_key=lambda: step("vast", "missing"))
        with mock.patch.object(setupcheck.autostart, "status", lambda name="resume": "unsupported"):
            ids = [s["id"] for s in setupcheck.status()["steps"]]
        self.assertNotIn("autostart", ids)
        self.assertNotIn("open_at_login", ids)


if __name__ == "__main__":
    unittest.main()

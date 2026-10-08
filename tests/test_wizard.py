# Простая настройка: этапы Tailscale по состоянию macOS, что уже готово, установка программ,
# последний шаг «доделываю сам». Всё поддельное: без установки, без сети, без Vast.

import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui         # noqa: E402
import setupcheck  # noqa: E402
from vastapi import VastgameError  # noqa: E402

EXT_OK = ("1 extension(s)\n--- com.apple.system_extension.network_extension\n"
          "*\t*\tW5364U7YZB\tio.tailscale.ipn.macsys.network-extension (1.102.4/101.102.4)\t"
          "Tailscale Network Extension\t[activated enabled]\n")
EXT_WAIT = EXT_OK.replace("[activated enabled]", "[activated waiting for user]")
EXT_REINSTALL = (EXT_OK.replace("[activated enabled]", "[terminated waiting to uninstall on reboot]")
                 + EXT_WAIT.splitlines()[-1] + "\n")
VPN_OK = '* (Connected)  2… VPN (io.tailscale.ipn.macsys) "Tailscale" [VPN:io.tailscale.ipn.macsys]\n'


class TestTailscalePhase(unittest.TestCase):
    def phase(self, status=None, ext="", vpn="", installed=True):
        outs = {"systemextensionsctl": ext, "scutil": vpn}
        with mock.patch.object(setupcheck.sys, "platform", "darwin"), \
             mock.patch.object(setupcheck.paths, "find", lambda n: "/x/tailscale" if installed else None), \
             mock.patch.object(setupcheck.paths, "tailscale_app", lambda: "/Applications/Tailscale.app" if installed else None), \
             mock.patch.object(setupcheck.paths, "tailscale_status", lambda: status), \
             mock.patch.object(setupcheck, "_out", lambda cmd: outs[cmd[0]]):
            return setupcheck.tailscale_phase()["phase"]

    def test_walks_through_every_phase(self):
        self.assertEqual(self.phase(installed=False), "missing")
        self.assertEqual(self.phase(), "start")                                    # ни разу не открывали
        self.assertEqual(self.phase(ext=EXT_WAIT), "extension")                    # ждёт переключателя
        self.assertEqual(self.phase(ext=EXT_OK), "vpn")                            # ждёт «Разрешить» про VPN
        self.assertEqual(self.phase(ext=EXT_OK, vpn=VPN_OK), "start")             # всё разрешено, запускается
        self.assertEqual(self.phase({"BackendState": "NeedsLogin"}), "login")
        self.assertEqual(self.phase({"BackendState": "Stopped"}), "stopped")
        self.assertEqual(self.phase({"BackendState": "Running", "CurrentTailnet": {"MagicDNSEnabled": False}}),
                         "magicdns")
        self.assertEqual(self.phase({"BackendState": "Running", "CurrentTailnet": {"MagicDNSEnabled": True}}), "ready")

    def test_reinstall_leaves_old_line(self):
        # После переустановки старая строка «waiting to uninstall» не должна сбивать: новая ждёт разрешения
        self.assertEqual(self.phase(ext=EXT_REINSTALL), "extension")


class TestWizardApi(unittest.TestCase):
    def setUp(self):
        self.api = gui.Api()
        # настоящая фоновая операция на этом Mac («Закончить» в открытом окне) не должна делать тесты «занятыми»
        p = mock.patch.object(gui.jobs, "state", lambda: None)
        p.start()
        self.addCleanup(p.stop)

    def test_state_collects_everything(self):
        with mock.patch.object(gui.sys, "platform", "darwin"), \
             mock.patch.object(gui.paths, "tailscale_app", lambda: "/Applications/Tailscale.app"), \
             mock.patch.object(gui.moonlight, "path", lambda: None), \
             mock.patch.object(gui.setupcheck, "vast_key", lambda: {"state": "ok", "key": "k", "params": {"balance": 3.2}}), \
             mock.patch.object(gui.setupcheck, "_vast_secrets", lambda: set()), \
             mock.patch.object(gui.setupcheck, "google", lambda s, sizes=True: {"state": "warn", "key": "setup.google.not_in_vast",
                                                                                "params": {"email": "a@b.c"}}), \
             mock.patch.object(gui.setupcheck, "tailscale_phase", lambda: {"phase": "login"}):
            s = self.api.wizard_state()
        self.assertEqual(s["programs"], {"tailscale": True, "moonlight": False})
        self.assertEqual(s["vast"], {"ok": True, "balance": 3.2})
        self.assertEqual(s["google"], {"ok": True, "email": "a@b.c"})    # «не в Vast» доделает последний шаг
        self.assertEqual(s["tailscale"]["phase"], "login")

    def test_state_without_vast_key_google_not_done(self):
        # Вошёл в Google, но ключа Vast нет — шаг Google не готов: токен некуда отправить
        ok = {"state": "ok", "key": "setup.google.ok", "params": {"email": "a@b.c"}}
        with mock.patch.object(gui.setupcheck, "vast_key", lambda: {"state": "missing", "key": "k", "params": {}}), \
             mock.patch.object(gui.setupcheck, "_vast_secrets", lambda: set()), \
             mock.patch.object(gui.setupcheck, "google", lambda s, sizes=True: ok), \
             mock.patch.object(gui.setupcheck, "tailscale_phase", lambda: {"phase": "missing"}):
            s = self.api.wizard_state()
        self.assertEqual((s["vast"]["ok"], s["google"]["ok"]), (False, False))

    def test_install_only_missing(self):
        done = []
        with mock.patch.object(gui.sys, "platform", "darwin"), \
             mock.patch.object(gui.moonlight, "path", lambda: "/Applications/Moonlight.app"), \
             mock.patch.object(gui.paths, "tailscale_app", lambda: None), \
             mock.patch.object(gui.installer, "moonlight", lambda: done.append("moonlight")), \
             mock.patch.object(gui.installer, "tailscale", lambda: done.append("tailscale")):
            self.assertEqual(self.api.install_programs(), {"ok": True})
            p = wait(self.api)
        self.assertIsNone(p["error"])
        self.assertEqual(done, ["tailscale"])                            # Moonlight уже стоял
        self.assertIn("wizard.install.tailscale", [e["key"] for e in p["events"]])

    def test_install_cancelled_password(self):
        def cancelled():
            raise VastgameError("Установка отменена.", key="install.cancelled")
        with mock.patch.object(gui.sys, "platform", "darwin"), \
             mock.patch.object(gui.moonlight, "path", lambda: "/M"), \
             mock.patch.object(gui.paths, "tailscale_app", lambda: None), \
             mock.patch.object(gui.installer, "tailscale", cancelled):
            self.api.install_programs()
            self.assertEqual(wait(self.api)["error"]["key"], "install.cancelled")

    def finish(self, template, secrets, autostart_status="off"):
        calls = []
        with mock.patch.object(gui, "my_template", lambda: template), \
             mock.patch.object(gui.setup, "save_template", lambda *a: calls.append(("create",) + a) or (True, "")), \
             mock.patch.object(gui.setup, "refresh_template", lambda: calls.append(("refresh",)) or (True, "")), \
             mock.patch.object(gui.sshkey, "ensure", lambda: calls.append(("sshkey",))), \
             mock.patch.object(gui.setupcheck, "_vast_secrets", lambda: secrets), \
             mock.patch.object(gui.setup, "send_google_to_vast", lambda: calls.append(("google",))), \
             mock.patch.object(gui.autostart, "status", lambda name="resume": autostart_status), \
             mock.patch.object(gui.autostart, "install", lambda name="resume": calls.append(("autostart", name)) or True):
            self.api.wizard_finish()
            p = wait(self.api)
        return p, calls

    def test_finish_from_scratch(self):
        p, calls = self.finish(None, set())
        self.assertIsNone(p["error"])
        self.assertEqual(calls, [("create", 100, "", "1920x1200", "0"), ("sshkey",), ("google",), ("autostart", "resume")])
        self.assertEqual([e["key"] for e in p["events"]],
                         ["wizard.finish.template", "wizard.finish.sshkey", "wizard.finish.google", "wizard.finish.autostart"])

    def test_finish_when_all_there(self):
        tpl = {"onstart": gui.setup.ONSTART}
        p, calls = self.finish(tpl, {"RCLONE_REFRESH_TOKEN", "RCLONE_CLIENT_ID", "RCLONE_CLIENT_SECRET"}, "on")
        self.assertEqual(calls, [("sshkey",)])                          # шаблон и доступ на месте, служба стоит

    def test_finish_outdated_template_refreshed(self):
        p, calls = self.finish({"onstart": "old"}, set())
        self.assertEqual(calls[0], ("refresh",))

    def test_finish_template_error_stops(self):
        with mock.patch.object(gui, "my_template", lambda: None), \
             mock.patch.object(gui.setup, "save_template", lambda *a: (False, "нет")), \
             mock.patch.object(gui.sshkey, "ensure", lambda: self.fail("дальше идти нельзя")):
            self.api.wizard_finish()
            self.assertEqual(wait(self.api)["error"]["key"], "setup.err.template")

    def test_tailscale_up_failure_explained(self):
        failed = subprocess.CompletedProcess([], 1, "", "requires mentioning all non-default flags")
        with mock.patch.object(gui.paths, "find", lambda n: "/x/tailscale"), \
             mock.patch.object(gui.paths, "run", lambda *a, **k: failed):
            self.assertEqual(self.api.tailscale_up()["error"]["key"], "wizard.ts.up_failed")


def wait(api, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        p = api.progress()
        if p and p["done"]:
            return p
        time.sleep(0.02)
    raise AssertionError("операция не закончилась")


if __name__ == "__main__":
    unittest.main()

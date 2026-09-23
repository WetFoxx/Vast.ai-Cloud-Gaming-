# Питоновская часть окна (gui.Api) с поддельным ядром: без денег, без сети, без окна.

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui  # noqa: E402
from vastapi import VastgameError  # noqa: E402

OFFER = {"id": 5, "gpu_name": "RTX 3090", "geolocation": "Latvia, LV", "dph_total": 0.2, "machine_id": 9}


def wait_done(api, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        p = api.progress()
        if p and p["done"]:
            return p
        time.sleep(0.02)
    raise AssertionError("операция не закончилась")


class TestPlay(unittest.TestCase):
    def setUp(self):
        self.rented = []
        self.gate = threading.Event()      # держит «запуск» открытым, пока тест не разрешит
        patches = {
            "require_no_machine": lambda: None,
            "template": lambda: {"hash_id": "h", "recommended_disk_space": 100, "env": ""},
            "offer_by_id": lambda oid, disk: dict(OFFER, id=oid),
            "rent": self.fake_rent,
            "wait_ready": self.fake_wait,
            "identity_notes": lambda r: [],
        }
        for name, value in patches.items():
            p = mock.patch.object(gui.core, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.api = gui.Api()

    def fake_rent(self, offer_id, body, offer=None):
        self.rented.append(offer_id)
        return 42, time.time()

    def fake_wait(self, iid, t0, o, ui):
        ui.stage("vast", "…")
        ui.say("x", "detail", key="core.vast_status", status="loading", msg="")
        ui.archive("steam-state", "down", "восстановление")
        self.gate.wait(5)
        ui.stage("ready", "…")
        return {"links": {"moonlight": "m", "sunshine": "s", "status": "st"}, "minutes": 7,
                "boot": (0, "ok", ""), "identity": None, "ip": "1", "dns": "m"}

    def test_normal_play(self):
        self.assertEqual(self.api.play(5), {"ok": True})
        time.sleep(0.1)
        p = self.api.progress()
        self.assertEqual(p["stage"], "vast")
        self.assertEqual(p["archives"]["steam-state"][0], "down")
        self.assertIn("core.vast_status", [e["key"] for e in p["events"]])
        self.gate.set()
        p = wait_done(self.api)
        self.assertIsNone(p["error"])
        self.assertEqual(p["result"]["minutes"], 7)
        self.assertEqual(self.rented, [5])

    def test_sync_toggles_remembered_and_sent(self):
        bodies, store = [], {}
        with mock.patch.object(gui.core, "rent", lambda oid, body, offer=None: bodies.append(body) or (42, time.time())), \
             mock.patch.object(gui.settings, "get", lambda k, d=None: store.get(k, d)), \
             mock.patch.object(gui.settings, "set", lambda k, v: store.__setitem__(k, v)):
            self.assertEqual(self.api._sync(), {"steam_games": False, "other_games": True, "saves": True})
            self.api.play(5, {"steam_games": True, "saves": False})
            self.gate.set()
            wait_done(self.api)
            self.assertEqual(self.api._sync(), {"steam_games": True, "other_games": True, "saves": False})
        self.assertEqual(bodies[0]["env"], {"SYNC_STEAM_GAMES": "1", "SYNC_OTHER_GAMES": "1", "SYNC_SAVES": "0"})

    def test_second_click_does_not_rent_twice(self):
        self.api.play(5)
        r = self.api.play(5)                         # пока первая аренда идёт
        self.assertEqual(r["error"]["key"], "busy")
        self.gate.set()
        wait_done(self.api)
        self.assertEqual(self.rented, [5])

    def test_existing_machine_blocks_rent(self):
        def running():
            raise VastgameError("есть машина", key="core.running", ids="1")
        with mock.patch.object(gui.core, "require_no_machine", running):
            self.api.play(5)
            p = wait_done(self.api)
        self.assertEqual(p["error"]["key"], "core.running")
        self.assertEqual(self.rented, [])            # аренды не было

    def test_question_goes_to_page_and_back(self):
        answers = []

        def wait_with_question(iid, t0, o, ui):
            answers.append(ui.confirm("Удалить?", key="core.ask_delete_unused"))
            raise VastgameError("Запуск не удался.", key="core.start_failed")
        with mock.patch.object(gui.core, "wait_ready", wait_with_question):
            self.api.play(5)
            for _ in range(100):
                q = self.api.progress()["question"]
                if q:
                    break
                time.sleep(0.02)
            self.assertEqual(q["key"], "core.ask_delete_unused")
            self.api.answer(q["id"] + 1, True)       # чужой номер вопроса — игнорируется
            time.sleep(0.05)
            self.assertEqual(answers, [])
            self.api.answer(q["id"], False)
            p = wait_done(self.api)
        self.assertEqual(answers, [False])
        self.assertEqual(p["error"]["key"], "core.start_failed")

    def test_closing_while_busy_is_blocked(self):
        self.api._window = mock.Mock()
        self.api.play(5)
        self.assertFalse(self.api._on_closing())    # идёт запуск — не закрывать молча
        self.gate.set()
        wait_done(self.api)
        self.assertTrue(self.api._on_closing())     # закончилось — можно


class TestStop(unittest.TestCase):
    def setUp(self):
        self.api = gui.Api()
        self.deleted = []

    def fake_stop(self, ui, dry_run=False):
        ui.stage("shutdown", "…")
        ui.archive("pfx--1", "up", "выгрузка")
        ui.say("Всё выгружено.", "ok", key="core.all_uploaded")
        if not ui.confirm("Удалить?", key="core.ask_delete", id=42):
            raise VastgameError("Не удаляю.", key="core.kept")
        self.deleted.append(42)
        ui.stage("deleted", "…")
        return {"iid": 42, "uploaded": ["pfx--1"], "deleted": True}

    def answer_question(self, yes):
        for _ in range(200):
            q = self.api.progress()["question"]
            if q:
                self.api.answer(q["id"], yes)
                return q
            time.sleep(0.01)
        raise AssertionError("вопроса не было")

    def test_stop_yes_deletes(self):
        with mock.patch.object(gui.core, "stop", self.fake_stop):
            self.assertEqual(self.api.stop(), {"ok": True})
            q = self.answer_question(True)
            p = wait_done(self.api)
        self.assertEqual((q["key"], q["params"]), ("core.ask_delete", {"id": 42}))
        self.assertEqual(p["kind"], "stop")
        self.assertEqual(p["result"], {"uploaded": ["pfx--1"], "deleted": True, "iid": 42})
        self.assertEqual(self.deleted, [42])

    def test_stop_no_keeps_machine(self):
        with mock.patch.object(gui.core, "stop", self.fake_stop):
            self.api.stop()
            self.answer_question(False)
            p = wait_done(self.api)
        self.assertEqual(p["error"]["key"], "core.kept")
        self.assertEqual(self.deleted, [])

    def test_emergency_only_when_idle_and_by_id(self):
        gate = threading.Event()
        calls = []
        with mock.patch.object(gui.core, "stop", lambda ui, dry_run=False: gate.wait(5) and {}), \
             mock.patch.object(gui.core, "emergency_delete", lambda mid: calls.append(mid) or True):
            self.api.stop()
            self.assertEqual(self.api.emergency_delete(42)["error"]["key"], "busy")   # идёт выгрузка
            gate.set()
            wait_done(self.api)
            self.assertEqual(self.api.emergency_delete("42"), {"ok": True, "gone": True})
        self.assertEqual(calls, [42])


class TestGames(unittest.TestCase):
    def test_listing_hides_files_and_remove_runs_in_background(self):
        api = gui.Api()
        data = {"games": [{"name": "pfx--1", "kind": "pfx", "title": "", "appid": "1", "bytes": 5, "mtime": "d",
                           "files": ["pfx--1.tar.zst"]}], "service": {"names": [], "bytes": 0}}
        removed = []
        with mock.patch.object(gui.cloudgames, "listing", lambda: data), \
             mock.patch.object(gui.cloudgames, "remove", lambda name, on_notice=None: removed.append(name) or "drive"):
            self.assertNotIn("files", api.games()["games"][0])
            self.assertEqual(api.games_remove("pfx--1"), {"ok": True})
            p = wait_done(api)
        self.assertEqual((p["kind"], p["result"], removed), ("games", {"how": "drive"}, ["pfx--1"]))


class TestSetupActions(unittest.TestCase):
    def test_install_uses_brew_cask_in_background(self):
        api, calls = gui.Api(), []

        class R:
            returncode, stdout, stderr = 0, "", ""
        with mock.patch.object(gui.paths, "find", lambda n: "/opt/homebrew/bin/brew"), \
             mock.patch.object(gui.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R()):
            self.assertEqual(api.install_tool("moonlight"), {"ok": True})
            wait_done(api)
        self.assertEqual(calls, [["/opt/homebrew/bin/brew", "install", "--cask", "moonlight"]])
        self.assertEqual(api.install_tool("rm -rf /")["error"]["text"], "unknown tool")   # только известные

    def test_no_brew(self):
        with mock.patch.object(gui.paths, "find", lambda n: None):
            self.assertEqual(gui.Api().install_tool("rclone")["error"]["key"], "setup.err.no_brew")

    def test_bad_vast_key_is_translated_error(self):
        with mock.patch.object(gui.setup, "check_key", lambda k: None):
            self.assertEqual(gui.Api().save_vast_key("nope")["error"]["key"], "setup.err.vast_key")

    def test_template_validation(self):
        api = gui.Api()
        self.assertEqual(api.save_template("5", "", "1920x1200")["error"]["key"], "setup.err.disk")
        self.assertEqual(api.save_template("100", "Рус", "1920x1200")["error"]["key"], "setup.err.lang")
        self.assertEqual(api.save_template("100", "", "big")["error"]["key"], "setup.err.res")

    def test_tailscale_key_format(self):
        self.assertEqual(gui.Api().save_tailscale_key("abc")["error"]["key"], "setup.err.ts_key")


class TestMoonlight(unittest.TestCase):
    def test_apps_and_stream_remembers_choice(self):
        api = gui.Api()
        store = {}
        started = []
        with mock.patch.object(gui.moonlight, "path", lambda: "/x/Moonlight"), \
             mock.patch.object(gui.moonlight, "apps", lambda host: ["Desktop", "Steam Big Picture"]), \
             mock.patch.object(gui.moonlight, "stream", lambda h, a: started.append((h, a)) or True), \
             mock.patch.object(gui.settings, "get", lambda k, d=None: store.get(k, d)), \
             mock.patch.object(gui.settings, "set", lambda k, v: store.__setitem__(k, v)):
            self.assertEqual(api.moonlight_apps("m")["selected"], "Desktop")          # по умолчанию
            self.assertEqual(api.open_moonlight("m", "Steam Big Picture"), {"ok": True})
            self.assertEqual(api.moonlight_apps("m")["selected"], "Steam Big Picture")  # запомнил
        self.assertEqual(started, [("m", "Steam Big Picture")])

    def test_not_installed(self):
        with mock.patch.object(gui.moonlight, "path", lambda: None):
            self.assertEqual(gui.Api().moonlight_apps("m"), {"installed": False})


if __name__ == "__main__":
    unittest.main()

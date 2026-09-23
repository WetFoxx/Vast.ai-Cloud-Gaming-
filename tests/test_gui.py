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

    def fake_rent(self, offer_id, body):
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


if __name__ == "__main__":
    unittest.main()

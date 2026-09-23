# Автотесты ядра (core.py) без денег и без сети: Vast, Tailscale, ssh и страница /json машины
# подменяются поддельными. Запуск: .venv/bin/python -m unittest discover -s tests -v

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core    # noqa: E402
import market  # noqa: E402
from vastapi import VastgameError  # noqa: E402


class FakeUI:
    """ui, который запоминает, что ему говорили, и отвечает на вопросы заранее заданным ответом."""

    def __init__(self, answer=True):
        self.answer, self.log, self.stages, self.questions, self.keys = answer, [], [], [], []

    def say(self, text, level="info", key=None, **params):
        self.log.append((level, text))
        self.keys.append(key)

    def stage(self, key, text):
        self.stages.append(key)

    def archive(self, name, state, text):
        self.log.append(("archive", f"{name} {state}"))

    def confirm(self, question, key=None, **params):
        self.questions.append(question)
        self.keys.append(key)
        return self.answer

    def tailscale(self, text):
        self.log.append(("tailscale", text))


class FakeProc:
    """Как subprocess.Popen для `wolf shutdown`: сразу закончился с кодом code."""

    def __init__(self, code=0):
        self.returncode, self.stdout = code, iter(["выгрузка...\n"])

    def poll(self):
        return self.returncode

    def kill(self):
        pass


OFFER = {"id": 111, "machine_id": 999, "dph_total": 0.2}
T0 = 1_000_000


class Base(unittest.TestCase):
    def setUp(self):
        # Чёрный список — во временный файл, чтобы тесты не трогали настоящий
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        os.unlink(tmp.name)
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        self.patch(market, "BAD_HOSTS", Path(tmp.name))
        self.patch(core.time, "sleep", lambda s: None)
        self.deleted = []

    def patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def fake_delete(self, alive):
        """DELETE instances/<id>/ — удаляет из списка alive."""
        def delete(iid):
            self.deleted.append(iid)
            alive.clear()
            return True, None
        return delete


class TestWaitReady(Base):
    def test_normal_start(self):
        statuses = iter(["loading", "created", "running"])
        self.patch(core, "call", lambda *a, **k: {"instances": {"actual_status": next(statuses), "status_msg": "",
                                                                 "extra_env": [["RES", "1920x1200"]]}})
        self.patch(core, "ts_node", lambda: (True, "100.1.1.1", "vastai-gaming.tail.ts.net"))
        json_pages = iter([
            {"boot": (T0 + 5, "up", "старт")},
            {"boot": (T0 + 60, "ok", "восстановление завершено"), "identity": (T0 + 10, "ok", "identity восстановлен")},
        ])
        last = {}

        def status(ip):
            nonlocal last
            last = next(json_pages, last)
            return last
        self.patch(core, "wolf_status", status)
        ui = FakeUI()
        r = core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(ui.stages, ["vast", "tailscale", "restore", "ready"])
        self.assertEqual(r["boot"][1], "ok")
        self.assertEqual(r["links"]["sunshine"], "https://vastai-gaming.tail.ts.net:47990")
        self.assertEqual(core.identity_notes(r), [])
        self.assertEqual(market.bad_hosts(), {})

    def test_host_error_marks_bad_and_deletes(self):
        msg = "Error response from daemon: failed to create task"
        alive = [42]

        def call(method, path, *a, **k):
            return {"instances": {"actual_status": "created", "status_msg": msg}}
        self.patch(core, "call", call)
        self.patch(core, "delete_instance", self.fake_delete(alive))
        ui = FakeUI(answer=True)
        with self.assertRaises(VastgameError):
            core.wait_ready(42, T0, OFFER, ui)
        self.assertIn("999", market.bad_hosts())          # хост в чёрном списке
        self.assertEqual(self.deleted, [42])               # удалён после «да»
        self.assertEqual(len(ui.questions), 1)

    def test_host_error_kept_if_no(self):
        self.patch(core, "call", lambda *a, **k: {"instances": {"actual_status": "exited", "status_msg": ""}})
        self.patch(core, "delete_instance", self.fake_delete([42]))
        with self.assertRaises(VastgameError):
            core.wait_ready(42, T0, OFFER, FakeUI(answer=False))
        self.assertEqual(self.deleted, [])                 # «нет» — не удаляем
        self.assertIn("999", market.bad_hosts())

    def test_renamed_node_and_lost_identity_notes(self):
        base = {"ip": "1", "links": {}, "boot": (0, "ok", ""), "minutes": 1}
        renamed = core.identity_notes(dict(base, dns="vastai-gaming-4.t.ts.net", identity=(0, "ok", "восстановлен")))
        self.assertEqual(renamed[0][0], "info")
        lost = core.identity_notes(dict(base, dns="vastai-gaming-6.t.ts.net", identity=(0, "ok", "нет в облаке")))
        self.assertEqual(lost[0][0], "warn")


class TestStatus(Base):
    def machine(self, vast="running", msg="", node=True, page=None):
        self.patch(core, "list_instances", lambda: [{"id": 7, "gpu_name": "RTX 3090", "geolocation": "Latvia, LV",
                                                     "dph_total": 0.2, "actual_status": vast, "status_msg": msg}])
        self.patch(core, "ts_node", lambda: (True, "100.1.1.1", "vastai-gaming.t.ts.net") if node else None)
        self.patch(core, "wolf_status", lambda ip: page)
        return core.status()

    def test_states(self):
        self.patch(core, "list_instances", lambda: [])
        self.assertEqual(core.status()["state"], core.NONE)
        self.assertEqual(self.machine(vast="loading")["stage"], "vast")
        self.assertEqual(self.machine(vast="created", msg="Error response from daemon")["state"], core.ERROR)
        self.assertEqual(self.machine(node=False)["stage"], "tailscale")
        self.assertEqual(self.machine(page={"boot": (1, "down", "Steam"), "identity": (1, "ok", "")})["stage"], "restore")
        ok = {"boot": (1, "ok", ""), "steam-state": (2, "ok", "")}
        r = self.machine(page=ok)
        self.assertEqual(r["state"], core.READY)
        self.assertEqual(r["machine"]["country"], "Latvia, LV")
        self.assertEqual(r["links"]["moonlight"], "vastai-gaming.t.ts.net")
        self.assertEqual(self.machine(page=dict(ok, **{"pfx--1": (3, "up", "")}))["state"], core.SYNCING)
        self.assertEqual(self.machine(page=dict(ok, **{"pfx--1": (3, "err", "")}))["state"], core.ERROR)
        self.assertEqual(self.machine(page={"boot": (1, "err", "ошибки")})["state"], core.ERROR)

    def test_kinds(self):
        """kind — что именно происходит; по нему окно собирает фразу на своём языке."""
        self.patch(core, "list_instances", lambda: [])
        self.assertEqual(core.status()["kind"], "none")
        self.patch(core, "list_instances", lambda: [{"id": 1}, {"id": 2}])
        self.assertEqual((core.status()["kind"], core.status()["count"]), ("many", 2))
        self.assertEqual(self.machine(vast="exited")["kind"], "vast_error")
        self.assertEqual(self.machine(vast="loading")["kind"], "vast")
        self.assertEqual(self.machine(node=False)["kind"], "tailscale")
        r = self.machine(page={"boot": (1, "down", ""), "identity": (1, "ok", ""), "pfx--1": (2, "down", "")})
        self.assertEqual((r["kind"], r["done"], r["busy"]), ("restore", 1, ["pfx--1"]))
        self.assertEqual(self.machine(page={"boot": (1, "err", "ошибки: pfx")})["detail"], "ошибки: pfx")
        ok = {"boot": (1, "ok", ""), "steam-state": (2, "ok", "")}
        self.assertEqual(self.machine(page=dict(ok, x=(3, "err", "")))["kind"], "sync_error")
        self.assertEqual(self.machine(page=dict(ok, x=(3, "up", "")))["kind"], "syncing")
        self.assertEqual(self.machine(page=ok)["kind"], "ready")


class TestStop(Base):
    def setup_machine(self, after, shutdown_code=0, answer=True):
        alive = [42]
        self.patch(core, "list_instances", lambda: [{"id": i} for i in alive])
        self.patch(core, "ts_node", lambda: (True, "100.1.1.1", "vastai-gaming.tail.ts.net"))

        def ssh_run(ip, command, timeout=180, on_notice=None):
            return (0, "ok\n") if command == "echo ok" else (0, f"{T0 + 2}\n")
        self.patch(core, "ssh_run", ssh_run)
        self.patch(core, "ssh", lambda ip, command: FakeProc(shutdown_code))
        before = {"boot": (T0 - 900, "ok", "восстановление завершено")}
        pages = iter([before, after])
        last = {}

        def status(ip):
            nonlocal last
            last = next(pages, last)
            return last
        self.patch(core, "wolf_status", status)
        self.patch(core, "delete_instance", self.fake_delete(alive))
        return FakeUI(answer=answer)

    def test_normal_stop_deletes(self):
        ui = self.setup_machine({"boot": (T0 - 900, "ok", ""), "steam-state": (T0 + 20, "ok", "выгружено")})
        r = core.stop(ui)
        self.assertEqual(r["uploaded"], ["steam-state"])
        self.assertTrue(r["deleted"])
        self.assertEqual(self.deleted, [42])
        self.assertEqual(ui.stages, ["shutdown", "verify", "deleted"])

    def test_failed_upload_not_deleted(self):
        ui = self.setup_machine({"steam-client": (T0 + 20, "err", "ошибка выгрузки")})
        with self.assertRaises(VastgameError) as e:
            core.stop(ui)
        self.assertIn("НЕ подтверждена", str(e.exception))
        self.assertEqual(self.deleted, [])
        self.assertEqual(ui.questions, [])                 # до вопроса об удалении не дошло

    def test_old_error_does_not_block(self):
        ui = self.setup_machine({"pfx--1": (T0 - 500, "err", "старая ошибка"), "steam-state": (T0 + 5, "ok", "")})
        core.stop(ui)
        self.assertEqual(self.deleted, [42])

    def test_no_means_no(self):
        ui = self.setup_machine({"steam-state": (T0 + 5, "ok", "")}, answer=False)
        with self.assertRaises(VastgameError):
            core.stop(ui)
        self.assertEqual(self.deleted, [])

    def test_shutdown_error_not_deleted(self):
        ui = self.setup_machine({"steam-state": (T0 + 5, "ok", "")}, shutdown_code=255)
        with self.assertRaises(VastgameError):
            core.stop(ui)
        self.assertEqual(self.deleted, [])

    def test_dry_run_changes_nothing(self):
        ui = self.setup_machine({})
        r = core.stop(ui, dry_run=True)
        self.assertIn("boot", r["archives"])
        self.assertEqual(self.deleted, [])
        self.assertEqual(ui.stages, [])


class TestRentalBody(unittest.TestCase):
    def test_sync_toggles_go_to_env(self):
        tpl = {"hash_id": "h"}
        self.assertNotIn("env", core.rental_body(tpl, 100))
        b = core.rental_body(tpl, 100, sync={"steam_games": False, "other_games": True, "saves": False})
        self.assertEqual(b["env"], {"SYNC_STEAM_GAMES": "0", "SYNC_OTHER_GAMES": "1", "SYNC_SAVES": "0"})
        self.assertEqual(core.rental_body(tpl, 100, games="1")["env"], {"SYNC_STEAM_GAMES": "1"})


class TestEmergency(Base):
    def test_deletes_only_expected(self):
        alive = [42]
        self.patch(core, "list_instances", lambda: [{"id": i} for i in alive])
        self.patch(core, "delete_instance", self.fake_delete(alive))
        with self.assertRaises(VastgameError) as e:
            core.emergency_delete(41)                  # в окне была другая машина
        self.assertEqual(e.exception.key, "core.emergency_mismatch")
        self.assertEqual(self.deleted, [])
        self.assertTrue(core.emergency_delete(42))
        self.assertEqual(self.deleted, [42])


if __name__ == "__main__":
    unittest.main()

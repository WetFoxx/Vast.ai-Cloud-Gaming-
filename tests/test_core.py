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


def _no_page():
    """Страница wolf (v3.15) в этих тестах «не отвечает» — проверяется путь по SSH (старые машины)."""
    p = mock.patch.object(core.wolfnode, "post_cmd", lambda *a, **k: None)
    p.start()
    return p


def setUpModule():
    unittest.addModuleCleanup(_no_page().stop)
    """Тесты не трогают настоящие файлы в папке настроек: настройки, историю, пометки хостов."""
    import tempfile
    import history, market, settings
    tmp = Path(tempfile.mkdtemp())
    for obj, name in [(settings, "FILE"), (history, "FILE"), (market, "BAD_HOSTS")]:
        p = mock.patch.object(obj, name, tmp / f"{obj.__name__}.json")
        p.start()
        unittest.addModuleCleanup(p.stop)
    # Замер задержки через Tailscale — не запускать настоящий tailscale ping
    p = mock.patch.object(core.latency, "measure", lambda ip: None)
    p.start()
    unittest.addModuleCleanup(p.stop)
    # Машина, которую ждём, по умолчанию есть у Vast (проверка раз в минуту не ходит в сеть)
    p = mock.patch.object(core, "_still_there", lambda iid: True)
    p.start()
    unittest.addModuleCleanup(p.stop)
    # Ссылка одобрения Tailscale с Drive — по умолчанию её нет (тесты не ходят в сеть)
    p = mock.patch.object(core.drive, "ts_login_link", lambda iid: None)
    p.start()
    unittest.addModuleCleanup(p.stop)


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
    def running_machine(self):
        self.patch(core, "call", lambda *a, **k: {"instances": {"actual_status": "running", "status_msg": "",
                                                                 "extra_env": []}})

    def test_deleted_on_site_while_waiting_stops_instead_of_hanging(self):
        # Живой случай 2026-09-25: машину удалили на сайте — запуск висел (Tailscale — до 20 минут,
        # восстановление — вечно, и после перезапуска окна снова). Теперь — две проверки «нет» и конец.
        self.running_machine()
        self.patch(core, "GONE_CHECK_SEC", 0)
        self.patch(core, "ts_node", lambda: None)                  # узел так и не появится
        answers = iter([True, None, False, False])                  # None — сбой связи, не считается
        self.patch(core, "_still_there", lambda iid: next(answers))
        ui = FakeUI()
        with self.assertRaises(VastgameError) as e:
            core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(e.exception.key, "core.machine_gone")
        self.assertEqual(self.deleted, [])                          # удалять нечего — её уже нет

    def test_one_missing_answer_is_not_enough(self):
        # Сразу после аренды машины может не быть в списке — одного «нет» мало
        self.running_machine()
        self.patch(core, "GONE_CHECK_SEC", 0)
        self.patch(core, "ts_node", lambda: (True, "100.1.1.1", "h"))
        answers = iter([False, True])
        self.patch(core, "_still_there", lambda iid: next(answers, True))
        self.patch(core, "wolf_status", lambda ip: {"boot": (T0 + 60, "ok", "готово")})
        self.assertEqual(core.wait_ready(42, T0, OFFER, FakeUI())["boot"][1], "ok")

    def test_cancel_deletes_machine_and_stops(self):
        self.running_machine()
        alive = [42]
        self.patch(core, "delete_instance", self.fake_delete(alive))
        self.patch(core, "_still_there", lambda iid: iid in alive)
        ui = FakeUI()
        ui.cancelled = lambda: True
        with self.assertRaises(VastgameError) as e:
            core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(e.exception.key, "core.start_cancelled")
        self.assertEqual(self.deleted, [42])

    def test_cancel_after_window_already_deleted(self):
        # Окно уже удалило машину: Vast на повторное удаление отвечает ошибкой — это не беда, её правда нет
        self.running_machine()
        self.patch(core, "_still_there", lambda iid: False)
        def already_gone(iid):
            raise VastgameError("HTTP 404", key="vast.http", code=404, msg="no such instance")
        self.patch(core, "delete_instance", already_gone)
        ui = FakeUI()
        ui.cancelled = lambda: True
        with self.assertRaises(VastgameError) as e:
            core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(e.exception.key, "core.start_cancelled")

    def test_cancel_right_after_rent_deletes_even_if_not_listed_yet(self):
        # Сразу после аренды Vast может не показывать машину в списке — удалить всё равно (иначе тратит деньги)
        self.running_machine()
        self.patch(core, "_still_there", lambda iid: False)
        self.patch(core, "delete_instance", self.fake_delete([42]))
        ui = FakeUI()
        ui.cancelled = lambda: True
        with self.assertRaises(VastgameError) as e:
            core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual((e.exception.key, self.deleted), ("core.start_cancelled", [42]))

    def test_cancel_delete_really_failed(self):
        self.running_machine()
        self.patch(core, "_still_there", lambda iid: True)
        self.patch(core, "delete_instance", lambda iid: (False, "busy"))
        ui = FakeUI()
        ui.cancelled = lambda: True
        with self.assertRaises(VastgameError) as e:
            core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(e.exception.key, "core.cancel_delete_failed")

    def test_restore_machine_stops_answering(self):
        self.running_machine()
        self.patch(core, "ts_node", lambda: (True, "100.1.1.1", "h"))
        pages = iter([{"boot": (T0 + 5, "up", "старт")}])          # один ответ, потом тишина
        self.patch(core, "wolf_status", lambda ip: next(pages, None))
        self.patch(core, "RESTORE_QUIET_MIN", -1)   # молчание сразу «слишком долгое»
        with self.assertRaises(VastgameError) as e:
            core.wait_ready(42, T0, OFFER, FakeUI())
        self.assertEqual(e.exception.key, "core.restore_no_answer")

    def test_first_machine_asks_to_approve_in_tailscale(self):
        # Нет личности и ключа: машина кладёт ссылку одобрения на Drive — приложение её отдаёт ui
        statuses = iter(["loading", "running"])
        self.patch(core, "call", lambda *a, **k: {"instances": {"actual_status": next(statuses, "running"),
                                                                 "status_msg": "", "extra_env": []}})
        polls = {"n": 0}

        def ts_node():
            polls["n"] += 1
            return (True, "100.1.1.1", "vastai-gaming.tail.ts.net") if polls["n"] > 2 else None   # одобрили
        self.patch(core, "ts_node", ts_node)
        self.patch(core.drive, "ts_login_link", lambda iid: "https://login.tailscale.com/a/abc" if iid == 42 else None)
        self.patch(core, "wolf_status", lambda ip: {"boot": (T0 + 60, "ok", "готово")})
        ui = FakeUI()
        core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(ui.keys.count("core.ts_approve"), 1)    # одна ссылка — одно событие, не на каждом круге
        self.assertEqual(ui.stages, ["vast", "tailscale", "restore", "ready"])

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
        market.mark_bad(999, "раньше не запустилась")      # прошлый сбой на этом хосте
        market.mark_bad(555, "другой хост")
        ui = FakeUI()
        r = core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(list(market.bad_hosts()), ["555"])   # запустилась — пометка снята только с неё
        market.clear_bad(555)
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
        self.assertIn("999", market.bad_hosts())          # хост помечен
        self.assertEqual(self.deleted, [42])               # удалён сам — на машине ничего не было
        self.assertEqual(ui.questions, [])                 # без вопроса: окно может быть закрыто

    def test_exited_machine_deleted_without_question(self):
        self.patch(core, "call", lambda *a, **k: {"instances": {"actual_status": "exited", "status_msg": ""}})
        self.patch(core, "delete_instance", self.fake_delete([42]))
        ui = FakeUI(answer=False)                          # даже «нет» на любой вопрос ничего не меняет
        with self.assertRaises(VastgameError):
            core.wait_ready(42, T0, OFFER, ui)
        self.assertEqual(self.deleted, [42])
        self.assertIn("999", market.bad_hosts())

    def test_failed_delete_is_reported(self):
        self.patch(core, "call", lambda *a, **k: {"instances": {"actual_status": "exited", "status_msg": ""}})
        self.patch(core, "delete_instance", lambda iid: (False, "busy"))
        ui = FakeUI()
        with self.assertRaises(VastgameError):
            core.wait_ready(42, T0, OFFER, ui)
        self.assertIn("core.delete_failed_manual", ui.keys)   # не удалилось — сказать, как удалить вручную

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
            if command == "wolf finish":                       # скрипт до v3.11: команды finish нет
                return 2, "использование: wolf boot|state|games|shutdown|restore ИМЯ..\n"
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
        self.assertEqual(ui.questions, [])

    def test_old_error_does_not_block(self):
        ui = self.setup_machine({"pfx--1": (T0 - 500, "err", "старая ошибка"), "steam-state": (T0 + 5, "ok", "")})
        core.stop(ui)
        self.assertEqual(self.deleted, [42])

    def test_verified_upload_deletes_without_question(self):
        ui = self.setup_machine({"steam-state": (T0 + 5, "ok", "")}, answer=False)
        core.stop(ui)
        self.assertEqual(self.deleted, [42])               # вопроса нет — удалено после проверки
        self.assertEqual(ui.questions, [])

    def test_status_page_gone_not_deleted(self):
        ui = self.setup_machine(None)                      # /json не ответил после выгрузки
        with self.assertRaises(VastgameError) as e:
            core.stop(ui)
        self.assertEqual(e.exception.key, "core.no_status_after")
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


class TestFinish(Base):
    """«Закончить» с wolf finish (v3.11): машина выгружает, проверяет и удаляет себя сама."""

    def setup_machine(self, pages, gone_after=None):
        """pages — ответы /json по очереди (последний повторяется); gone_after — после скольких
        проверок списка Vast машина пропадает (удалила себя); None — не пропадает."""
        self.alive = [42]
        checks = {"n": 0}

        def list_instances():
            checks["n"] += 1
            if gone_after is not None and checks["n"] > gone_after:
                self.alive.clear()
            return [{"id": i} for i in self.alive]
        self.patch(core, "list_instances", list_instances)
        self.patch(core, "ts_node", lambda: (True, "100.1.1.1", "vastai-gaming.tail.ts.net"))
        self.commands = []

        def ssh_run(ip, command, timeout=180, on_notice=None):
            self.commands.append(command)
            if command == "echo ok":
                return 0, "ok\n"
            if command == "date +%s":
                return 0, f"{T0 + 2}\n"
            if command == "wolf finish":
                return 0, "finish: запущено\n"
            return 1, "?"
        self.patch(core, "ssh_run", ssh_run)
        self.patch(core, "ssh", lambda ip, command: self.fail("старый путь по SSH не должен запускаться"))
        before = {"boot": (T0 - 900, "ok", "восстановление завершено")}
        it = iter([before] + pages)
        last = {}

        def status(ip):
            nonlocal last
            last = next(it, last)
            return last
        self.patch(core, "wolf_status", status)
        self.patch(core, "delete_instance", self.fake_delete(self.alive))
        return FakeUI(answer=False)

    def test_machine_deletes_itself(self):
        up = {"finish": (T0 + 3, "up", "выгрузка"), "steam-state": (T0 + 5, "up", "выгрузка")}
        done = {"finish": (T0 + 60, "ok", "self: всё выгружено"), "steam-state": (T0 + 50, "ok", "выгружено")}
        ui = self.setup_machine([up, done], gone_after=1)
        r = core.stop(ui)
        self.assertEqual(r, {"iid": 42, "uploaded": ["steam-state"], "deleted": True})
        self.assertIn("wolf finish", self.commands)
        self.assertEqual(self.deleted, [])                 # удалила себя сама — отсюда не удаляли
        self.assertIn("core.self_deleting", ui.keys)
        self.assertEqual(ui.stages, ["shutdown", "verify", "deleted"])

    def test_machine_could_not_delete_itself_app_deletes(self):
        done = {"finish": (T0 + 60, "ok", "self: всё выгружено"), "steam-state": (T0 + 50, "ok", "")}
        ui = self.setup_machine([done])                    # так и не пропала из списка
        core.stop(ui)
        self.assertEqual(self.deleted, [42])
        self.assertIn("core.self_delete_failed", ui.keys)

    def test_no_key_on_machine_app_deletes(self):
        done = {"finish": (T0 + 60, "ok", "app: ключа нет"), "steam-state": (T0 + 50, "ok", "")}
        ui = self.setup_machine([done])
        core.stop(ui)
        self.assertEqual(self.deleted, [42])
        self.assertNotIn("core.self_deleting", ui.keys)

    def test_machine_says_not_confirmed(self):
        bad = {"finish": (T0 + 60, "err", "выгрузка не подтверждена: pfx--1"), "pfx--1": (T0 + 50, "err", "")}
        ui = self.setup_machine([bad])
        with self.assertRaises(VastgameError) as e:
            core.stop(ui)
        self.assertEqual(e.exception.key, "core.finish_failed")
        self.assertEqual(self.deleted, [])

    def test_app_double_checks_machine_verdict(self):
        # Машина сказала «ok», но в статусах свежая ошибка — отсюда не удаляем
        odd = {"finish": (T0 + 60, "ok", "app: всё выгружено"), "pfx--1": (T0 + 50, "err", "ошибка")}
        ui = self.setup_machine([odd])
        with self.assertRaises(VastgameError) as e:
            core.stop(ui)
        self.assertEqual(e.exception.key, "core.upload_unconfirmed")
        self.assertEqual(self.deleted, [])

    def test_network_errors_do_not_abort(self):
        # Интернет у человека пропадает: Vast и /json не отвечают — ждём дальше, машина работает сама
        up = {"finish": (T0 + 3, "up", "выгрузка")}
        done = {"finish": (T0 + 60, "ok", "app: всё выгружено"), "steam-state": (T0 + 50, "ok", "")}
        ui = self.setup_machine([up, None, None, done])
        real = core.list_instances
        flaky = {"n": 0}

        def list_instances():
            flaky["n"] += 1
            if flaky["n"] in (2, 3):
                raise VastgameError("нет сети")
            return real()
        self.patch(core, "list_instances", list_instances)
        self.patch(core.time, "time", iter(range(0, 10**6, 70)).__next__)   # каждая проверка — через 70 с
        core.stop(ui)
        self.assertEqual(self.deleted, [42])

    def test_resumed_follow_checks_from_finish_start(self):
        # Продолжение: finish уже шёл до нашего t0; ошибка архива случилась после его старта —
        # её нельзя списать в «старые»
        running = {"finish": (T0 - 300, "up", "выгрузка")}
        done = {"finish": (T0 + 60, "ok", "app: всё выгружено"), "pfx--1": (T0 - 100, "err", "ошибка")}
        ui = self.setup_machine([running, done])
        with self.assertRaises(VastgameError) as e:
            core.stop(ui)
        self.assertEqual(e.exception.key, "core.upload_unconfirmed")
        self.assertEqual(self.deleted, [])

    def test_offline_the_whole_time_machine_gone_is_success(self):
        # Wi-Fi выключен: /json не отвечает, а когда связь вернулась — машины уже нет (удалила себя)
        up = {"finish": (T0 + 3, "up", "выгрузка"), "steam-state": (T0 + 5, "ok", "выгружено")}
        ui = self.setup_machine([up, None], gone_after=2)
        r = core.stop(ui)
        self.assertEqual((r["deleted"], r["gone"], r["uploaded"]), (True, True, ["steam-state"]))
        self.assertEqual(self.deleted, [])
        self.assertIn("core.finished_offline", ui.keys)

    def test_noisy_answer_still_recognised(self):
        # Перед «finish: запущено» пришла служебная строка — это всё равно новый путь, без старой выгрузки
        done = {"finish": (T0 + 60, "ok", "app: всё выгружено"), "steam-state": (T0 + 50, "ok", "")}
        ui = self.setup_machine([done])
        real = core.ssh_run
        self.patch(core, "ssh_run", lambda ip, command, **k: (0, "Running as unit: wolf-finish.service\nfinish: запущено\n")
                   if command == "wolf finish" else real(ip, command, **k))
        core.stop(ui)
        self.assertEqual(self.deleted, [42])
        self.assertNotIn("core.old_script", ui.keys)

    def test_unknown_answer_but_finish_in_status(self):
        # Ответ непонятный, но в /json появилась запись finish — значит, wolf finish идёт
        running = {"finish": (T0 + 3, "up", "выгрузка")}
        done = {"finish": (T0 + 60, "ok", "app: всё выгружено"), "steam-state": (T0 + 50, "ok", "")}
        ui = self.setup_machine([running, done])
        real = core.ssh_run
        self.patch(core, "ssh_run", lambda ip, command, **k: (1, "что-то странное\n")
                   if command == "wolf finish" else real(ip, command, **k))
        core.stop(ui)
        self.assertEqual(self.deleted, [42])
        self.assertNotIn("core.old_script", ui.keys)

    def test_old_finish_result_is_ignored(self):
        # Итог прошлого finish (до t0) не считается — ждём новый
        old = {"finish": (T0 - 100, "ok", "self: старое")}
        new = {"finish": (T0 + 60, "ok", "app: всё выгружено"), "steam-state": (T0 + 50, "ok", "")}
        ui = self.setup_machine([old, old, new])
        core.stop(ui)
        self.assertEqual(self.deleted, [42])


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

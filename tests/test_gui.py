# Питоновская часть окна (gui.Api) с поддельным ядром: без денег, без сети, без окна.

import sys
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui  # noqa: E402
from vastapi import VastgameError  # noqa: E402


APPLOG = []


def setUpModule():
    """Тесты не трогают настоящие файлы в папке настроек: настройки, историю, пометки хостов."""
    import tempfile
    import history, market, settings
    tmp = Path(tempfile.mkdtemp())
    for obj, name in [(settings, "FILE"), (history, "FILE"), (market, "BAD_HOSTS"), (market, "STARTS"),
                      (gui.jobs, "STATE"), (gui.jobs, "LOCK"), (gui.jobs, "LOG"), (gui.jobs, "CANCEL"),
                      (gui.latency, "FILE")]:
        p = mock.patch.object(obj, name, tmp / f"{obj.__name__}.{name}")
        p.start()
        unittest.addModuleCleanup(p.stop)
    # app.log — в память (открытие Moonlight пишет туда ход), а не в настоящую папку настроек
    p = mock.patch.object(settings, "applog", APPLOG.append)
    p.start()
    unittest.addModuleCleanup(p.stop)
    # Фоновый процесс в тестах — поток с той же работой (jobs.work: блокировка, job.json, уведомление)
    for obj, name, value in [(gui.jobs, "_launch", lambda kind, args: threading.Thread(
                                  target=gui.jobs.work, args=(kind, args), daemon=True).start()),
                             (gui.jobs.notify, "send", lambda title, text: SENT.append((title, text))),
                             (gui.moonlight, "native_display", lambda: None),      # не звать system_profiler
                             # названия игр — не спрашивать сайт Steam и не писать кэш в настоящую папку
                             (gui.steamnames, "for_archives", lambda archives: {})]:
        p = mock.patch.object(obj, name, value)
        p.start()
        unittest.addModuleCleanup(p.stop)


SENT = []   # уведомления, которые «показали»

OFFER = {"id": 5, "gpu_name": "RTX 3090", "geolocation": "Latvia, LV", "dph_total": 0.2, "machine_id": 9}


def wait_done(api, timeout=5, session=False):
    """Дождаться конца операции: session=True — «Играть»/«Закончить» (фоновый процесс), иначе — в окне."""
    end = time.time() + timeout
    while time.time() < end:
        p = api.session_progress() if session else api.progress()
        if p and p["done"]:
            return p
        time.sleep(0.02)
    raise AssertionError("операция не закончилась")


class TestOffers(unittest.TestCase):
    def setUp(self):
        row = dict(OFFER, country="Latvia, LV", _traffic=0.01, _total=0.21, reliability=0.99)
        for obj, name, value in [(gui.core, "require_no_machine", lambda: None),
                                 (gui.core, "template", lambda: {"recommended_disk_space": 100}),
                                 (gui.core, "balance", lambda: 3.0),
                                 (gui.market, "search", lambda disk, docker=False: [row])]:
            p = mock.patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        self.row = row

    def test_last_machine_free(self):
        last = {"gpu": "RTX 3060", "country": "Ohio, US", "start": 5, "offer": dict(self.row, id=77)}
        with mock.patch.object(gui.market, "last_machine", lambda disk, docker=False: last):
            d = gui.Api().offers()
        self.assertEqual(d["last"]["offer"]["id"], 77)
        self.assertEqual(d["offers"][0]["id"], 5)

    def test_all_machines_with_ping_speed_and_filters(self):
        rows = [dict(self.row, id=i, inet_down=500 + i, inet_up=300, geolocation="Japan, JP") for i in range(40)]
        with mock.patch.object(gui.market, "search", lambda disk, docker=False: rows), \
             mock.patch.object(gui.market, "last_machine", lambda disk, docker=False: None):
            d = gui.Api().offers()
        self.assertEqual(len(d["offers"]), 40)                        # все, а не 10 лучших — страницы на экране
        self.assertEqual(d["page_size"], gui.OFFERS_PAGE)
        o = d["offers"][0]
        self.assertEqual((o["cc"], o["net_down"], o["net_up"]), ("JP", 500, 300))
        self.assertEqual(o["traffic_tb"], 0)                          # трафик для фильтра Docker, $ за ТБ
        self.assertIn(o["ping"]["kind"], ("estimate", "measured", "region"))
        self.assertEqual(d["filters"], gui.OFFER_FILTERS)             # ничего не выбрано — всё по умолчанию

    def test_filters_remembered_and_cleaned(self):
        api = gui.Api()
        self.assertEqual(api.save_offer_filters({"countries": ["JP", 5], "gpus": ["RTX 3060", None], "speed": "300",
                                                 "ping": -1, "sort": "evil", "dir": "sideways"}), {"ok": True})
        self.assertEqual(api._offer_filters(), {"countries": ["JP"], "gpus": ["RTX 3060"], "speed": 300, "ping": 0,
                                                "traffic": 1, "sort": "price", "dir": "asc"})
        api.save_offer_filters({"traffic": 0.1})
        self.assertEqual(api._offer_filters()["traffic"], 0.1)
        api.save_offer_filters({"traffic": 7})                       # не из списка — по умолчанию
        self.assertEqual(api._offer_filters()["traffic"], 1)
        api.save_offer_filters({"sort": "net", "dir": "desc"})
        self.assertEqual((api._offer_filters()["sort"], api._offer_filters()["dir"]), ("net", "desc"))
        self.assertIn("error", api.save_offer_filters("nope"))

    def test_disk_limits(self):
        with mock.patch.object(gui.market, "last_machine", lambda disk, docker=False: None):
            self.assertEqual(gui.Api().offers(20)["disk"], gui.DISK_MIN)
            self.assertEqual(gui.Api().offers(5000)["disk"], gui.DISK_MAX)
            self.assertEqual(gui.Api().offers(300)["disk"], 300)

    def test_last_machine_check_fails_but_list_shows(self):
        def boom(disk, docker=False):
            raise VastgameError("429")
        with mock.patch.object(gui.market, "last_machine", boom):
            d = gui.Api().offers()
        self.assertIsNone(d["last"])
        self.assertEqual(len(d["offers"]), 1)


class TestPlay(unittest.TestCase):
    def setUp(self):
        self.rented = []
        self.gate = threading.Event()      # держит «запуск» открытым, пока тест не разрешит
        self.addCleanup(self.gate.set)
        patches = {
            "require_no_machine": lambda: None,
            "template": lambda: {"hash_id": "h", "recommended_disk_space": 100, "env": ""},
            "offer_by_id": lambda oid, disk, docker=False: dict(OFFER, id=oid),
            "rent": self.fake_rent,
            "wait_ready": self.fake_wait,
            "identity_notes": lambda r: [],
            # Забег машин: Vast сразу показывает арендованную машину (42) работающей
            "list_instances": lambda: [{"id": 42, "actual_status": "running", "status_msg": ""}],
            "_delete_quietly": lambda iid: (True, None),
        }
        for name, value in patches.items():
            p = mock.patch.object(gui.jobs.core, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.wait_idle)
        self.api = gui.Api()
        SENT.clear()

    def wait_idle(self):
        """Следующий тест начинается, когда фоновая работа этого закончилась."""
        for _ in range(250):
            if not gui.jobs.running() and not (gui.jobs.state() or {}).get("active"):
                return
            time.sleep(0.02)

    def fake_rent(self, offer_id, body, offer=None):
        self.rented.append(offer_id)
        self.body = body
        return 42, time.time()

    def fake_wait(self, iid, t0, o, ui, docker=False):
        ui.stage("vast", "…")
        ui.say("x", "detail", key="core.vast_status", status="loading", msg="")
        ui.archive("steam-state", "down", "восстановление")
        self.gate.wait(5)
        ui.stage("ready", "…")
        return {"links": {"moonlight": "m", "sunshine": "s", "status": "st"}, "minutes": 7,
                "boot": (0, "ok", ""), "identity": None, "ip": "1", "dns": "m"}

    def test_normal_play_in_background_and_notification(self):
        self.assertEqual(self.api.play(5, None, None, "ru"), {"ok": True})
        for _ in range(100):
            p = self.api.session_progress()
            if p["stage"] == "vast" and p["archives"]:
                break
            time.sleep(0.02)
        self.assertEqual(p["archives"]["steam-state"][0], "down")
        self.assertIn("core.vast_status", [e["key"] for e in p["events"]])
        self.gate.set()
        p = wait_done(self.api, session=True)
        self.assertIsNone(p["error"])
        self.assertEqual(p["result"]["minutes"], 7)
        self.assertEqual(self.rented, [5])
        self.assertEqual(SENT[0][0], "Машина готова")                 # уведомление на языке окна

    def test_spare_list_reaches_background_job(self):
        # Запасные машины со страницы (на случай, если Vast не запустит выбранную) — в аргументах процесса
        self.gate.set()
        self.assertEqual(self.api.play(5, None, None, "ru", [7, "x", 5, 8]), {"ok": True})
        wait_done(self.api, session=True)
        self.assertEqual(gui.jobs.load()["args"]["spare"], [7, 8])    # мусор и сама выбранная — выброшены

    def cancellable_wait(self, iid, t0, o, ui, docker=False):
        """Как core.wait_ready при отмене: увидел флаг — закончил с core.start_cancelled."""
        ui.stage("vast", "…")
        for _ in range(250):
            if ui.cancelled():
                raise gui.VastgameError("отменён", key="core.start_cancelled", id=iid)
            time.sleep(0.02)
        raise AssertionError("отмену не увидели")

    def test_cancel_deletes_machine_and_job_ends_quietly(self):
        deleted = []
        with mock.patch.object(gui.jobs.core, "wait_ready", self.cancellable_wait), \
             mock.patch.object(gui.core, "_still_there", lambda iid: iid not in deleted), \
             mock.patch.object(gui.core, "_delete_quietly", lambda iid: deleted.append(iid) or (True, None)):
            self.api.play(5, None, None, "ru")
            for _ in range(100):
                if (self.api.session_progress() or {}).get("stage") == "vast":
                    break
                time.sleep(0.02)
            self.assertEqual(self.api.cancel_start(), {"ok": True, "iid": 42, "iids": [42]})
            p = wait_done(self.api, session=True)
        self.assertEqual(deleted, [42])                               # окно удалило сразу
        self.assertEqual(p["error"]["key"], "core.start_cancelled")
        self.assertEqual(SENT, [])                                    # отменил сам — уведомлять незачем
        self.assertFalse(gui.jobs.CANCEL.exists())                   # флаг убран — следующему запуску не мешает

    def test_old_cancel_flag_does_not_cancel_new_start(self):
        gui.jobs.CANCEL.write_text("1")                              # осталось от прошлого раза
        self.api.play(5)
        for _ in range(100):
            if (self.api.session_progress() or {}).get("stage") == "vast":
                break
            time.sleep(0.02)
        self.assertFalse(gui.jobs.CANCEL.exists())
        self.gate.set()
        self.assertIsNone(wait_done(self.api, session=True)["error"])

    def test_cancel_when_nothing_starts(self):
        gui.jobs.STATE.unlink(missing_ok=True)
        self.assertEqual(self.api.cancel_start(), {"ok": True, "iid": None, "iids": []})
        self.assertFalse(gui.jobs.CANCEL.exists())

    def test_progress_since_and_new_window_sees_same_job(self):
        self.api.play(5)
        self.gate.set()
        p = wait_done(self.api, session=True)
        again = gui.Api().session_progress(p["next"])                  # «окно открыли заново»
        self.assertEqual((again["kind"], again["events"], again["done"]), ("play", [], True))

    def test_disk_from_slider_goes_to_rental(self):
        self.gate.set()
        self.assertEqual(self.api.play(5, None, 300), {"ok": True})
        wait_done(self.api, session=True)
        self.assertEqual(self.body["disk"], 300)
        self.assertEqual(gui.settings.get("disk"), 300)            # запомнен (во временном файле теста)

    def test_sync_toggles_remembered_and_sent(self):
        bodies, store = [], {}
        with mock.patch.object(gui.jobs.core, "rent", lambda oid, body, offer=None: bodies.append(body) or (42, time.time())), \
             mock.patch.object(gui.settings, "get", lambda k, d=None: store.get(k, d)), \
             mock.patch.object(gui.settings, "set", lambda k, v: store.__setitem__(k, v)):
            self.assertEqual(self.api._sync(), {"steam_games": False, "other_games": True, "saves": True})
            self.api.play(5, {"steam_games": True, "saves": False})
            self.gate.set()
            wait_done(self.api, session=True)
            self.assertEqual(self.api._sync(), {"steam_games": True, "other_games": True, "saves": False})
        self.assertEqual(bodies[0]["env"], {"SYNC_STEAM_GAMES": "1", "SYNC_OTHER_GAMES": "1", "SYNC_SAVES": "0"})

    def test_second_click_does_not_rent_twice(self):
        self.api.play(5)
        r = self.api.play(5)                         # пока первая аренда идёт
        self.assertEqual(r["error"]["key"], "busy")
        self.assertEqual(gui.Api().play(5)["error"]["key"], "busy")    # и из другого окна тоже
        self.gate.set()
        wait_done(self.api, session=True)
        self.assertEqual(self.rented, [5])

    def test_existing_machine_blocks_rent(self):
        def running():
            raise VastgameError("есть машина", key="core.running", ids="1")
        with mock.patch.object(gui.jobs.core, "require_no_machine", running):
            self.api.play(5, lang="en")
            p = wait_done(self.api, session=True)
        self.assertEqual(p["error"]["key"], "core.running")
        self.assertEqual(self.rented, [])            # аренды не было
        self.assertEqual(SENT[0][0], "Machine didn't start")

    def test_unexpected_question_is_no(self):
        answers = []

        def wait_with_question(iid, t0, o, ui, docker=False):
            answers.append(ui.confirm("Удалить?"))
            raise VastgameError("Запуск не удался.", key="core.start_failed")
        with mock.patch.object(gui.jobs.core, "wait_ready", wait_with_question):
            self.api.play(5)
            p = wait_done(self.api, session=True)
        self.assertEqual(answers, [False])           # спросить некого — безопасное «нет»
        self.assertEqual(p["error"]["key"], "core.start_failed")

    def test_closing_window_does_not_stop_play(self):
        self.api._window = mock.Mock()
        self.api.play(5)
        self.assertTrue(self.api._on_closing())     # запуск в фоне — окно закрывается спокойно
        self.gate.set()
        wait_done(self.api, session=True)

    def test_result_shown_once_after_reopen(self):
        self.gate.set()
        self.api.play(5)
        wait_done(self.api, session=True)
        with mock.patch.object(gui.core, "status", lambda: {"machine": None, "archives": {}}), \
             mock.patch.object(gui.core, "balance", lambda: 5.0):
            self.assertEqual(gui.Api().state()["task"], {"kind": "play", "active": False, "unseen": True})
            self.api.session_seen()
            self.assertFalse(gui.Api().state()["task"]["unseen"])     # посмотрел — больше не показываем

    def test_interrupted_play_resumes_without_renting_again(self):
        # «Компьютер выключился» после аренды: в job.json есть номер машины, процесса нет
        job = gui.jobs.FileJob("play", pid=999999, args={"offer_id": 5, "lang": "ru"})
        job.say("x", key="core.vast_status")
        job.set_meta(iid=42, t0=time.time(), offer=dict(OFFER, id=5))
        self.gate.set()
        with mock.patch.object(gui.jobs.core, "list_instances", lambda: [{"id": 42}]):
            p = self.api.session_progress()                      # окно открыли — продолжение само
            self.assertFalse(p["done"])
            p = wait_done(self.api, session=True)
        self.assertIsNone(p["error"])
        self.assertEqual(self.rented, [])                        # вторую машину НЕ арендовали
        keys = [e["key"] for e in gui.jobs.load()["events"]]
        self.assertEqual(keys[:2], ["core.vast_status", "progress.resuming"])   # один непрерывный ход
        self.assertEqual(SENT[-1][0], "Машина готова")

    def test_interrupted_before_rent_is_not_resumed(self):
        gui.jobs.FileJob("play", pid=999999, args={"offer_id": 5})   # номера машины нет
        p = self.api.session_progress()
        self.assertEqual((p["done"], p["error"]["key"]), (True, "progress.lost"))
        self.assertEqual(self.rented, [])

    def test_resume_gives_up_after_limit(self):
        job = gui.jobs.FileJob("play", pid=999999, args={"offer_id": 5})
        job.set_meta(iid=42, t0=0, offer=OFFER)
        d = gui.jobs.load()
        d["resumes"] = gui.jobs.MAX_RESUMES
        gui.jobs.save(d)
        p = self.api.session_progress()
        self.assertEqual(p["error"]["key"], "progress.lost")     # не крутиться вечно


class TestApproveLink(unittest.TestCase):
    def test_background_job_opens_approve_link_once(self):
        opened = []
        with mock.patch("webbrowser.open", lambda u: opened.append(u)):
            job = gui.jobs.FileJob("play", pid=1)
            for _ in range(3):   # ядро сообщает ссылку раз, но и повтор не должен открывать вкладки
                job.say("x", "tailscale", key="core.ts_approve", url="https://login.tailscale.com/a/abc")
            job.say("y", "tailscale", key="core.other", url="https://login.tailscale.com/a/zzz")
        self.assertEqual(opened, ["https://login.tailscale.com/a/abc"])


class TestStop(unittest.TestCase):
    def setUp(self):
        self.api = gui.Api()
        self.deleted = []
        SENT.clear()

    def fake_stop(self, ui, dry_run=False):
        ui.stage("shutdown", "…")
        ui.archive("pfx--1", "up", "выгрузка")
        ui.say("Всё выгружено.", "ok", key="core.all_uploaded")
        self.deleted.append(42)
        ui.stage("deleted", "…")
        return {"iid": 42, "uploaded": ["pfx--1"], "deleted": True}

    def test_stop_deletes_without_question(self):
        with mock.patch.object(gui.jobs.core, "stop", self.fake_stop):
            self.assertEqual(self.api.stop("ru"), {"ok": True})
            p = wait_done(self.api, session=True)
        self.assertEqual(p["kind"], "stop")
        self.assertIsNone(p["question"])
        self.assertEqual(p["result"], {"uploaded": ["pfx--1"], "deleted": True, "iid": 42, "gone": False})
        self.assertEqual(self.deleted, [42])
        self.assertEqual(SENT[-1][0], "Игра закончена")

    def test_failed_upload_notifies(self):
        def failing(ui, dry_run=False):
            raise VastgameError("не подтверждена", key="core.upload_unconfirmed", names=["pfx--1"])
        with mock.patch.object(gui.jobs.core, "stop", failing):
            self.api.stop("ru")
            p = wait_done(self.api, session=True)
        self.assertEqual(p["error"]["key"], "core.upload_unconfirmed")
        self.assertEqual(SENT[-1][0], "Завершить не удалось")

    def test_interrupted_stop_resumes(self):
        calls = []
        with mock.patch.object(gui.jobs.core, "stop", lambda ui, dry_run=False: calls.append(1) or self.fake_stop(ui)):
            gui.jobs.FileJob("stop", pid=999999, args={"lang": "ru"})
            self.api.session_progress()
            p = wait_done(self.api, session=True)
        self.assertEqual((calls, p["result"]["deleted"]), ([1], True))

    def test_resumed_stop_machine_already_gone(self):
        def gone(ui, dry_run=False):
            raise VastgameError("нет", key="core.no_instances")
        with mock.patch.object(gui.jobs.core, "stop", gone):
            gui.jobs.FileJob("stop", pid=999999, args={})
            self.api.session_progress()
            p = wait_done(self.api, session=True)
        self.assertIsNone(p["error"])                            # машина закончила и удалила себя сама
        self.assertTrue(p["result"]["gone"])

    def test_fresh_stop_without_machine_is_error(self):
        def gone(ui, dry_run=False):
            raise VastgameError("нет", key="core.no_instances")
        with mock.patch.object(gui.jobs.core, "stop", gone):
            self.api.stop()
            p = wait_done(self.api, session=True)
        self.assertEqual(p["error"]["key"], "core.no_instances")   # не продолжение — это ошибка

    def test_emergency_only_when_idle_and_by_id(self):
        gate = threading.Event()
        calls = []
        with mock.patch.object(gui.jobs.core, "stop", lambda ui, dry_run=False: gate.wait(5) and {"iid": 1, "uploaded": [], "deleted": True}), \
             mock.patch.object(gui.core, "emergency_delete", lambda mid: calls.append(mid) or True):
            self.api.stop()
            self.assertEqual(self.api.emergency_delete(42)["error"]["key"], "busy")   # идёт выгрузка
            gate.set()
            wait_done(self.api, session=True)
            self.assertEqual(self.api.emergency_delete("42"), {"ok": True, "gone": True})
        self.assertEqual(calls, [42])


class TestSafe(unittest.TestCase):
    def test_unexpected_error_reaches_the_page(self):
        with mock.patch.object(gui.setupcheck, "status", mock.Mock(side_effect=ValueError("сломалось"))), \
             mock.patch.object(gui, "_log_error", lambda where: None):
            r = gui.Api().setup_status()
        self.assertEqual(r["error"]["text"], "ValueError: сломалось")    # не исключение, а ответ странице

    def test_keychain_denied_is_explained(self):
        import keyring.errors
        with mock.patch.object(gui.setupcheck, "status", mock.Mock(side_effect=keyring.errors.KeyringLocked("-128"))), \
             mock.patch.object(gui, "_log_error", lambda where: None):
            self.assertEqual(gui.Api().setup_status()["error"]["key"], "keychain.denied")


class TestReset(unittest.TestCase):
    def test_reset_removes_keys_services_and_settings(self):
        # Как стираются ключи — tests/test_keystore.py (TestWipe); здесь — что сброс делает всё остальное
        tmp = Path(tempfile.mkdtemp()) / "cfg"
        tmp.mkdir()
        (tmp / "settings.json").write_text("{}")
        removed = []
        with mock.patch.object(gui.setup.keystore, "wipe", lambda: {"deleted": 3, "left": 1}), \
             mock.patch.object(gui.setup.settings, "config_dir", lambda: tmp), \
             mock.patch("autostart.uninstall", lambda name: removed.append(name)):
            r = gui.Api().reset_local()
        self.assertEqual((r, removed), ({"ok": True, "deleted": 3, "left": 1}, ["resume", "app"]))
        self.assertFalse(tmp.exists())


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

    def test_closing_while_local_job_asks(self):
        api = gui.Api()
        api._window = mock.Mock()
        gate = threading.Event()
        with mock.patch.object(gui.cloudgames, "remove", lambda name, on_notice=None: gate.wait(5) and "drive"):
            api.games_remove("pfx--1")
            self.assertFalse(api._on_closing())      # убирает игру в самом окне — сначала спросить
            gate.set()
            wait_done(api)
        self.assertTrue(api._on_closing())


class TestSetupActions(unittest.TestCase):
    def test_install_uses_brew_cask_in_background(self):
        api, calls = gui.Api(), []

        class R:
            returncode, stdout, stderr = 0, "", ""
        with mock.patch.object(gui.paths, "find", lambda n: "/opt/homebrew/bin/brew"), \
             mock.patch.object(gui.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R()):
            self.assertEqual(api.install_tool("rclone"), {"ok": True})
            wait_done(api)
        self.assertEqual(calls, [["/opt/homebrew/bin/brew", "install", "rclone"]])

    def test_tailscale_and_moonlight_use_official_installer_not_brew(self):
        api, used = gui.Api(), []
        with mock.patch.object(gui.installer, "tailscale", lambda: used.append("ts") or True), \
             mock.patch.object(gui.installer, "moonlight", lambda: used.append("ml") or True), \
             mock.patch.object(gui.subprocess, "Popen", lambda cmd, **k: None), \
             mock.patch.object(gui.setup, "tailscale_wait_ready", lambda: "Running"), \
             mock.patch.object(gui.paths, "find", lambda n: None):      # Homebrew нет — не мешает
            api.install_tool("tailscale_app")
            wait_done(api)
            api.install_tool("moonlight")
            wait_done(api)
        self.assertEqual(used, ["ts", "ml"])
        self.assertEqual(api.install_tool("rm -rf /")["error"]["text"], "unknown tool")   # только известные

    def test_tailscale_install_leads_to_sign_in(self):
        api, steps = gui.Api(), []
        with mock.patch.object(gui.installer, "tailscale", lambda: steps.append("install") or True), \
             mock.patch.object(gui.subprocess, "Popen", lambda cmd, **k: steps.append("open " + cmd[-1])), \
             mock.patch.object(gui.setup, "tailscale_wait_ready", lambda: steps.append("wait") or "NeedsLogin"), \
             mock.patch.object(gui.setup, "tailscale_login", lambda open_url: steps.append("login") or True):
            api.install_tool("tailscale_app")
            p = wait_done(api)
        self.assertIsNone(p["error"])
        self.assertEqual(steps, ["install", "open Tailscale", "wait", "login"])
        self.assertEqual([e["key"] for e in p["events"]],
                         ["setup.ts_install.installing", "setup.ts_install.approve", "setup.ts_login.waiting"])

    def test_sign_in_page_can_be_reopened(self):
        api, opened = gui.Api(), []
        self.assertEqual(api.tailscale_reopen_login()["error"]["key"], "setup.err.ts_no_link")
        with mock.patch.object(gui.webbrowser, "open", lambda u: opened.append(u)), \
             mock.patch.object(gui.setup, "tailscale_login", lambda open_url: open_url("https://login.tailscale.com/a/x") or True):
            api.tailscale_login()
            wait_done(api)
            self.assertEqual(api.tailscale_reopen_login(), {"ok": True})
        self.assertEqual(opened, ["https://login.tailscale.com/a/x"] * 2)     # открыли и открыли ещё раз

    def test_tailscale_already_signed_in_after_install(self):
        api, steps = gui.Api(), []
        with mock.patch.object(gui.installer, "tailscale", lambda: True), \
             mock.patch.object(gui.subprocess, "Popen", lambda cmd, **k: None), \
             mock.patch.object(gui.setup, "tailscale_wait_ready", lambda: "Running"), \
             mock.patch.object(gui.setup, "tailscale_login", lambda open_url: steps.append("login")):
            api.install_tool("tailscale_app")
            wait_done(api)
        self.assertEqual(steps, [])                  # уже в сети — страницу входа не открываем

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


NATIVE = {"w": 2560, "h": 1600, "fps": 60}


class TestStreamSettings(unittest.TestCase):
    """Разрешение и битрейт трансляции: по умолчанию — родное разрешение экрана и «авто»."""

    def setUp(self):
        self.store, self.started = {}, []
        for obj, name, value in [(gui.moonlight, "native_display", lambda: NATIVE),
                                 (gui.moonlight, "path", lambda: "/x/Moonlight"),
                                 (gui.moonlight, "apps", lambda host: ["Desktop"]),
                                 (gui.moonlight, "stream", lambda h, a, size=None, fps=None, bitrate=None:
                                  self.started.append((size, fps, bitrate)) or True),
                                 (gui.settings, "get", lambda k, d=None: self.store.get(k, d)),
                                 (gui.settings, "set", lambda k, v: self.store.__setitem__(k, v))]:
            p = mock.patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        self.api = gui.Api()

    def test_native_first_and_default(self):
        s = self.api.stream_settings()
        self.assertEqual(s["resolutions"][0], {"value": "native", "w": 2560, "h": 1600, "native": True})
        self.assertFalse(any(o["native"] for o in s["resolutions"][1:]))
        self.assertTrue(all(o["w"] <= 2560 and o["h"] <= 1600 for o in s["resolutions"]))   # не больше родного
        self.assertEqual((s["resolution"], s["bitrate"]), ("native", 0))
        self.api.open_moonlight("m", "Desktop")
        self.assertEqual(self.started, [((2560, 1600), 60, None)])     # битрейт подберёт Moonlight

    def test_choice_remembered_and_passed(self):
        self.assertEqual(self.api.save_stream_settings("1920x1080", 30), {"ok": True})
        self.api.open_moonlight("m", "Desktop")
        self.assertEqual(self.started, [((1920, 1080), 60, 30)])
        self.assertEqual(self.api.stream_settings()["resolution"], "1920x1080")

    def test_bad_values_rejected(self):
        self.assertIn("error", self.api.save_stream_settings("9999x9999", 0))
        self.assertIn("error", self.api.save_stream_settings("native", 7))
        self.assertEqual(self.store, {})

    def test_unknown_screen_leaves_it_to_moonlight(self):
        with mock.patch.object(gui.moonlight, "native_display", lambda: None):
            api = gui.Api()
            self.assertEqual(api.stream_settings()["resolution"], "moonlight")
            api.open_moonlight("m", "Desktop")
        self.assertEqual(self.started, [(None, None, None)])

    def test_saved_native_follows_the_screen(self):
        # Выбрано «родное» — на другом мониторе это его родное разрешение, а не прежние цифры
        self.api.save_stream_settings("native", 0)
        with mock.patch.object(gui.moonlight, "native_display", lambda: {"w": 3840, "h": 2160, "fps": 120}):
            api = gui.Api()
            api.open_moonlight("m", "Desktop")
        self.assertEqual(self.started[-1], ((3840, 2160), 120, None))


class TestMoonlight(unittest.TestCase):
    def test_apps_and_stream_remembers_choice(self):
        api = gui.Api()
        store = {}
        started = []
        with mock.patch.object(gui.moonlight, "path", lambda: "/x/Moonlight"), \
             mock.patch.object(gui.moonlight, "apps", lambda host: ["Desktop", "Steam Big Picture"]), \
             mock.patch.object(gui.moonlight, "stream", lambda h, a, *opt: started.append((h, a)) or True), \
             mock.patch.object(gui.settings, "get", lambda k, d=None: store.get(k, d)), \
             mock.patch.object(gui.settings, "set", lambda k, v: store.__setitem__(k, v)):
            self.assertEqual(api.moonlight_apps("m")["selected"], "Desktop")          # по умолчанию
            self.assertEqual(api.open_moonlight("m", "Steam Big Picture"), {"ok": True})
            self.assertEqual(api.moonlight_apps("m")["selected"], "Steam Big Picture")  # запомнил
        self.assertEqual(started, [("m", "Steam Big Picture")])

    def test_not_paired_pairs_first_then_streams(self):
        steps, seen = [], iter([None])                              # до связки не видит, после — видит
        with mock.patch.object(gui.moonlight, "path", lambda: "/x/Moonlight"), \
             mock.patch.object(gui.moonlight, "apps", lambda host: next(seen, ["Desktop"])), \
             mock.patch.object(gui.wolfnode, "ts_node", lambda: (True, "100.1.1.1", "h")), \
             mock.patch.object(gui.pairing, "pair", lambda host, ip, on_notice=None: steps.append(("pair", host, ip)) or True), \
             mock.patch.object(gui.moonlight, "stream", lambda h, a, *opt: steps.append(("stream", h, a)) or True), \
             mock.patch.object(gui.settings, "set", lambda k, v: None):
            self.assertEqual(gui.Api().open_moonlight("h", "Desktop"), {"ok": True})
        self.assertEqual(steps, [("pair", "h", "100.1.1.1"), ("stream", "h", "Desktop")])

    def test_pairing_not_seen_is_repeated(self):
        # Живой случай 2026-09-27: связка прошла, а Moonlight машину «не видел» — повторить связку за человека
        steps, seen = [], iter([None, None, None, None])            # до связки и три проверки после неё
        del APPLOG[:]
        with mock.patch.object(gui.moonlight, "path", lambda: "/x/Moonlight"), \
             mock.patch.object(gui.moonlight, "apps", lambda host: next(seen, ["Desktop"])), \
             mock.patch.object(gui.moonlight, "log_tail", lambda n=12: "Warning: x"), \
             mock.patch.object(gui.time, "sleep", lambda s: None), \
             mock.patch.object(gui.wolfnode, "ts_node", lambda: (True, "100.1.1.1", "h")), \
             mock.patch.object(gui.pairing, "pair", lambda host, ip, on_notice=None: steps.append("pair") or True), \
             mock.patch.object(gui.moonlight, "stream", lambda h, a, *opt: steps.append("stream") or True), \
             mock.patch.object(gui.settings, "set", lambda k, v: None):
            self.assertEqual(gui.Api().open_moonlight("h", "Desktop"), {"ok": True})
        self.assertEqual(steps, ["pair", "pair", "stream"])
        self.assertTrue(any("связываю ещё раз" in x and "Warning: x" in x for x in APPLOG), APPLOG)

    def test_second_click_while_pairing_does_not_stream_twice(self):
        # Живой тест 2026-09-25: два нажатия во время связки → две трансляции, обе оборвались
        api, streams = gui.Api(), []
        inside, release = threading.Event(), threading.Event()
        def slow_pair(host, ip, on_notice=None):
            inside.set(); release.wait(5)
        with mock.patch.object(gui.moonlight, "path", lambda: "/x/Moonlight"), \
             mock.patch.object(gui.moonlight, "apps", lambda host: None), \
             mock.patch.object(gui.wolfnode, "ts_node", lambda: (True, "100.1.1.1", "h")), \
             mock.patch.object(gui.pairing, "pair", slow_pair), \
             mock.patch.object(gui.moonlight, "log_tail", lambda n=12: ""), \
             mock.patch.object(gui.time, "sleep", lambda s: None), \
             mock.patch.object(gui.moonlight, "stream", lambda h, a, *opt: streams.append(a) or True), \
             mock.patch.object(gui.settings, "set", lambda k, v: None):
            first = threading.Thread(target=api.open_moonlight, args=("h", "Desktop"))
            first.start(); inside.wait(5)
            self.assertEqual(api.open_moonlight("h", "Desktop"), {"ok": True, "busy": True})
            release.set(); first.join(5)
            self.assertEqual(streams, ["Desktop"])
            self.assertEqual(api.open_moonlight("h", "Desktop"), {"ok": True})   # потом — снова можно

    def test_not_installed(self):
        with mock.patch.object(gui.moonlight, "path", lambda: None):
            self.assertEqual(gui.Api().moonlight_apps("m"), {"installed": False})


if __name__ == "__main__":
    unittest.main()

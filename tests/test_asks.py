# Выбор игр и вопросы машины (wolf-setup.sh v4.6) на стороне приложения: список «одна строка на игру», запомненный
# выбор, VG_SYNC в аренде, вопросы «новая / удалённая игра» — уведомление, ответ машине, служба без окна.
# Без сети и без машины: Tailscale, страница /json и команды машине — поддельные.

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asks  # noqa: E402
import cloudgames  # noqa: E402
import core  # noqa: E402
import gui  # noqa: E402
import jobs  # noqa: E402
import settings  # noqa: E402
import steamnames  # noqa: E402
import wolfnode  # noqa: E402
from vastapi import VastgameError  # noqa: E402

SENT = []


def setUpModule():
    tmp = Path(tempfile.mkdtemp())
    for obj, name in [(settings, "FILE"), (steamnames, "CACHE"), (steamnames, "COVERS"),
                      (asks, "WINDOW_LOCK"), (asks, "CARD_LOCK")]:
        p = mock.patch.object(obj, name, tmp / f"{obj.__name__}.{name}")
        p.start()
        unittest.addModuleCleanup(p.stop)
    p = mock.patch.object(asks.notify, "send", lambda title, text, sound=False: SENT.append((title, text, sound)))
    p.start()
    unittest.addModuleCleanup(p.stop)


def listing(*items):
    """Как cloudgames.listing(): games — [{name, kind, title, appid, bytes}]."""
    games = []
    for name, size in items:
        kind, rest = name.split("--", 1)
        games.append({"name": name, "kind": kind, "title": rest if kind != "pfx" else "",
                      "appid": rest if kind == "pfx" else None, "bytes": size})
    return {"games": games}


def rec(ts, state, text=""):
    return wolfnode.Rec((ts, state, text))


class TestRows(unittest.TestCase):
    def test_one_row_per_game_saves_joined_tools_hidden(self):
        data = listing(("sgame--The Blood of Dawnwalker", 53_000), ("pfx--3751260", 21),
                       ("sgame--Proton - Experimental", 900), ("pfx--1493710", 1),        # Proton и его «сохранения»
                       ("sgame--SteamLinuxRuntime_4", 700), ("game--Witcher", 48_000), ("pfx--2931553111", 9),
                       ("pfx--1091500", 41))                                              # сохранения без игры
        index = [{"archive": "sgame--The Blood of Dawnwalker", "appid": "3751260", "name": "The Blood of Dawnwalker"},
                 {"archive": "game--Witcher", "appid": "2931553111", "name": "The Witcher 3", "kind": "folder"},
                 {"archive": "sgame--Some Tool", "appid": "777", "name": "Tool", "tool": True}]
        rows = cloudgames.rows(data, index)
        self.assertEqual([r["id"] for r in rows], ["sgame--The Blood of Dawnwalker", "game--Witcher", "pfx--1091500"])
        dawn, witcher, cyber = rows
        self.assertEqual((dawn["saves"], dawn["kind"], dawn["title"]), ("pfx--3751260", "steam", "The Blood of Dawnwalker"))
        self.assertEqual((witcher["saves"], witcher["kind"], witcher["title"]), ("pfx--2931553111", "folder", "The Witcher 3"))
        self.assertEqual((cyber["game"], cyber["kind"], cyber["title"]), (None, "saves", ""))   # название спросит окно

    def test_without_index_game_and_saves_are_separate(self):
        # Облако до v4.6 (списка игр ещё нет): чья это папка сохранений — не знаем, строки раздельные
        rows = cloudgames.rows(listing(("sgame--Hades II", 5), ("pfx--1145350", 1)), [])
        self.assertEqual([r["id"] for r in rows], ["sgame--Hades II", "pfx--1145350"])


class TestSystemFiles(unittest.TestCase):
    def test_steam_system_files_marked(self):
        # Экран «Игры в облаке»: Proton, Runtime… и их «сохранения» — отдельной группой «системные файлы Steam»
        arch = {n: {"size": 1, "mtime": "2026-10-01", "files": [n + ".tar.zst"]} for n in
                ("sgame--Proton - Experimental", "sgame--SteamLinuxRuntime_4", "pfx--1493710", "sgame--Hades II",
                 "pfx--1145350", "game--Mine", "identity")}
        with mock.patch.object(cloudgames.drive, "token", lambda: "t"), \
             mock.patch.object(cloudgames.drive, "archives", lambda: arch), \
             mock.patch.object(cloudgames.steamnames, "for_archives", lambda a: {}):
            games = cloudgames.listing()["games"]
        system = sorted(g["name"] for g in games if g["system"])
        self.assertEqual(system, ["pfx--1493710", "sgame--Proton - Experimental", "sgame--SteamLinuxRuntime_4"])


class TestSelection(unittest.TestCase):
    def setUp(self):
        settings.FILE.unlink(missing_ok=True)
        self.rows = [{"game": "sgame--A", "saves": "pfx--1", "kind": "steam"},
                     {"game": "game--B", "saves": None, "kind": "folder"},
                     {"game": None, "saves": "pfx--2", "kind": "saves"}]

    def test_first_time_from_old_toggles(self):
        settings.set("sync", {"steam_games": True, "other_games": False, "saves": True})
        sel = cloudgames.selection(self.rows)
        self.assertEqual(sel, {"games": {"sgame--A": True, "game--B": False}, "saves": {"pfx--1": True, "pfx--2": True}})
        # запомнено: новые игры дальше — выключены, прежние тумблеры больше не читаются
        settings.set("sync", {"steam_games": True, "other_games": True, "saves": False})
        rows = self.rows + [{"game": "sgame--New", "saves": "pfx--3", "kind": "steam"}]
        sel = cloudgames.selection(rows)
        self.assertEqual(sel["games"], {"sgame--A": True, "game--B": False, "sgame--New": False})
        self.assertEqual(sel["saves"]["pfx--3"], True)                 # сохранения — по умолчанию вкл.

    def test_old_saves_off_carried(self):
        settings.set("sync", {"steam_games": False, "other_games": True, "saves": False})
        self.assertEqual(cloudgames.selection(self.rows)["saves"], {"pfx--1": False, "pfx--2": False})

    def test_new_user_nothing_selected(self):
        self.assertEqual(cloudgames.selection(self.rows)["games"], {"sgame--A": False, "game--B": False})

    def test_remember_merges_and_filters(self):
        cloudgames.remember({"games": {"sgame--A": True, "evil/../x": True, "pfx--9": True}, "saves": {"pfx--1": False}})
        out = cloudgames.remember({"games": {"game--B": True}})
        self.assertEqual(out, {"games": ["game--B", "sgame--A"], "saves_off": ["pfx--1"]})

    def test_answers_change_selection(self):
        cloudgames.remember({"games": {"sgame--A": True}})
        cloudgames.remember_answer("new", "sgame--N", True)
        cloudgames.remember_answer("new", "sgame--M", False)
        cloudgames.remember_answer("del", "sgame--A", True)            # удалили отовсюду — забыть
        cloudgames.remember_answer("del", "game--K", False)            # осталась в облаке — но не качать
        self.assertEqual(settings.get("sync_sel")["games"], {"sgame--N": True, "sgame--M": False, "game--K": False})


class TestMods(unittest.TestCase):
    """[v5.0] Моды в облаке: какой архив от какой игры, ответы «сохранять моды?» и выбор «с модами» — к машине."""

    def setUp(self):
        settings.FILE.unlink(missing_ok=True)

    def test_listing_names_game_by_archive(self):
        arch = {"mod--2424420--Avatar Legends The Fighting Game": {"size": 340, "mtime": "2026-10-01", "files": ["x"]},
                "mod--bad": {"size": 1, "mtime": "", "files": ["y"]}, "steam-state": {"size": 5, "mtime": "", "files": []}}
        with mock.patch.object(cloudgames.drive, "token", lambda: "t"), \
             mock.patch.object(cloudgames.drive, "archives", lambda: arch), \
             mock.patch.object(cloudgames.steamnames, "for_archives", lambda a: {}):
            data = cloudgames.listing()
            mods = cloudgames.mods()
        [g] = [g for g in data["games"] if g["kind"] == "mod"]
        self.assertEqual((g["appid"], g["title"], g["bytes"]), ("2424420", "Avatar Legends The Fighting Game", 340))
        self.assertIn("mod--bad", data["service"]["names"])                 # не по образцу — не трогаем
        self.assertEqual(mods, {"2424420": {"name": "mod--2424420--Avatar Legends The Fighting Game", "bytes": 340,
                                            "mtime": "2026-10-01"}})

    def test_answers_go_to_next_machine(self):
        cloudgames.remember_answer("mod", "mod--10", True)
        cloudgames.remember_answer("mod", "mod--20", False)
        cloudgames.remember_answer("mod", "mod--30", True)
        cloudgames.remember_answer("mod", "mod--30", False)                 # передумал
        self.assertEqual(cloudgames.mods_answers(), {"yes": ["10"], "no": ["20", "30"]})
        body = core.rental_body({"hash_id": "h"}, 100, sync={"select": {"games": [], "saves_off": [],
                                                                       "mods": cloudgames.mods_answers()},
                                                            "play": {"appid": "10", "account": "76561198059309761", "mods": True}})
        self.assertEqual(json.loads(base64.b64decode(body["env"]["VG_SYNC"]))["mods"], {"yes": ["10"], "no": ["20", "30"]})
        self.assertTrue(json.loads(base64.b64decode(body["env"]["VG_PLAY"]))["mods"])

    def test_mod_question_is_recognised(self):
        s = {"ask--mod--mod--2424420": (5, "ask", "Avatar Legends"), "mod--2424420--Avatar Legends": (6, "ok", "")}
        self.assertEqual(core.asks_of(s), [{"kind": "mod", "archive": "mod--2424420", "name": "Avatar Legends", "ts": 5}])


class TestRental(unittest.TestCase):
    def test_vg_sync_survives_spaces(self):
        body = core.rental_body({"hash_id": "h"}, 100, sync={"steam_games": False, "other_games": True, "saves": True,
                                "select": {"games": ["game--The Blood of Dawnwalker", "sgame--Hades II"],
                                           "saves_off": ["pfx--1"]}})
        env = body["env"]
        self.assertEqual((env["SYNC_STEAM_GAMES"], env["SYNC_OTHER_GAMES"], env["SYNC_SAVES"]), ("0", "1", "1"))
        self.assertNotIn(" ", env["VG_SYNC"])
        # [v4.8] игры Steam качает Steam: в выборе их нет, машине сказано "steam": "store"
        self.assertEqual(json.loads(base64.b64decode(env["VG_SYNC"])),
                         {"games": ["game--The Blood of Dawnwalker"], "saves_off": ["pfx--1"], "steam": "store",
                          "mods": {"yes": [], "no": []}})
        self.assertNotIn("VG_PLAY", env)

    def test_library_game_goes_to_machine(self):
        body = core.rental_body({"hash_id": "h"}, 100, sync={"select": {"games": [], "saves_off": []},
                                "play": {"appid": "2424420", "account": "76561198059309761", "name": "Avatar Legends"}})
        self.assertEqual(json.loads(base64.b64decode(body["env"]["VG_PLAY"])),
                         {"appid": "2424420", "account": "76561198059309761", "name": "Avatar Legends", "mods": False})

    def test_without_select_no_vg_sync(self):
        self.assertNotIn("VG_SYNC", core.rental_body({"hash_id": "h"}, 100, sync={"saves": True})["env"])


class TestAsks(unittest.TestCase):
    def setUp(self):
        settings.FILE.unlink(missing_ok=True)
        SENT.clear()
        self.page = {"boot": rec(1, "ok"), "sgame--X": rec(2, "ok"),
                     "ask--new--sgame--Hades II": rec(20, "ask", "Hades II"),
                     "ask--del--game--Witcher": rec(10, "ask", "The Witcher 3")}
        node = lambda: (True, "100.1.2.3", "vastai-gaming.ts.net")    # noqa: E731
        for obj, name, value in [(wolfnode, "ts_node", node), (wolfnode, "wolf_status", lambda ip: self.page),
                                 (core, "ts_node", node), (core, "wolf_status", lambda ip: self.page)]:
            p = mock.patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        self.calls = []

        def run(ip, cmd, args=(), stdin=None, timeout=120, on_notice=None, ssh=None):
            self.calls.append((ip, cmd, list(args)))
            kind, archive, yes = args
            self.page.pop(f"ask--{kind}--{archive}", None)             # машина убирает вопрос
            return 0, ""
        p = mock.patch.object(wolfnode, "run", run); p.start(); self.addCleanup(p.stop)

    def test_pending_in_order_and_not_archives(self):
        ip, items = asks.pending()
        self.assertEqual(ip, "100.1.2.3")
        self.assertEqual([(a["kind"], a["archive"], a["name"]) for a in items],
                         [("del", "game--Witcher", "The Witcher 3"), ("new", "sgame--Hades II", "Hades II")])

    def test_status_keeps_asks_out_of_archives(self):
        with mock.patch.object(core, "list_instances",
                               lambda: [{"id": 1, "actual_status": "running", "gpu_name": "RTX", "geolocation": "x",
                                         "dph_total": 0.1, "machine_id": 5}]):
            st = core.status()
        self.assertEqual(len(st["asks"]), 2)
        self.assertFalse(any(n.startswith("ask--") for n in st["archives"]))
        self.assertEqual(st["state"], "ready")                         # вопрос — не «синхронизация»

    def test_announce_once_with_sound(self):
        _, items = asks.pending()
        new = asks.fresh(items)
        asks.mark_seen(new)
        asks.announce(new, "ru")
        self.assertEqual(asks.fresh(items), [])                        # второй раз — не сообщать
        self.assertEqual(len(SENT), 1)
        self.assertTrue(SENT[0][2])                                    # со звуком
        self.assertIn("2", SENT[0][0])
        # новый вопрос о той же игре позже (другое время) — снова сообщить
        self.page["ask--new--sgame--Hades II"] = rec(99, "ask", "Hades II")
        self.assertEqual([a["archive"] for a in asks.fresh(asks.pending()[1])], ["sgame--Hades II"])

    def test_answer_goes_to_machine_and_selection(self):
        asks.answer("100.1.2.3", "new", "sgame--Hades II", True)
        self.assertEqual(self.calls, [("100.1.2.3", "answer", ["new", "sgame--Hades II", "yes"])])
        self.assertTrue(settings.get("sync_sel")["games"]["sgame--Hades II"])

    def test_bad_answers_refused_before_machine(self):
        for kind, archive in [("kill", "sgame--X"), ("new", "pfx--1"), ("del", "identity"), ("new", 5)]:
            with self.assertRaises(VastgameError):
                asks.answer("100.1.2.3", kind, archive, True)
        with self.assertRaises(VastgameError) as e:
            asks.answer(None, "new", "sgame--X", True)
        self.assertEqual(e.exception.key, "ask.no_machine")
        self.assertEqual(self.calls, [])

    def test_machine_error_not_remembered(self):
        with mock.patch.object(wolfnode, "run", lambda *a, **k: (1, "answer: не понял")):
            with self.assertRaises(VastgameError) as e:
                asks.answer("100.1.2.3", "del", "game--Witcher", True)
        self.assertEqual(e.exception.key, "ask.failed")
        self.assertIsNone(settings.get("sync_sel"))

    def test_window_answer_checks_question_is_gone(self):
        api = gui.Api()
        with mock.patch.object(gui.Api, "card_close", lambda self: None):
            self.assertEqual(api.ask_answer("del", "game--Witcher", False), {"ok": True, "left": 1})
            self.assertEqual(api.ask_answer("del", "game--Witcher", False), {"ok": True, "left": 1})   # уже нет — ок
            # машина ответила «0», а вопрос остался (другой ответ ещё шёл) — не молчать
            with mock.patch.object(wolfnode, "run", lambda *a, **k: (0, "")):
                r = api.ask_answer("new", "sgame--Hades II", True)
        self.assertEqual(r["error"]["key"], "ask.failed")
        self.assertEqual(len(self.calls), 1)

    def test_service_shows_card_only_without_window(self):
        spawned = []
        with mock.patch.object(jobs.subprocess, "Popen", lambda cmd, **k: spawned.append(cmd)):
            with open(asks.WINDOW_LOCK, "a") as lock:                   # «окно открыто»
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
                jobs.watch_asks()
                self.assertEqual((spawned, SENT), ([], []))
            jobs.watch_asks()                                          # окно закрыто
            jobs.watch_asks()                                          # через 5 минут — те же вопросы
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0][-1], "ask")
        self.assertEqual(len(SENT), 1)

    def test_no_machine_nothing(self):
        with mock.patch.object(wolfnode, "ts_node", lambda: None):
            self.assertEqual(asks.pending(), (None, []))


class TestSyncList(unittest.TestCase):
    def test_rows_with_choice_titles_and_covers(self):
        settings.FILE.unlink(missing_ok=True)
        settings.set("sync_sel", {"games": {"sgame--Hades II": True}, "saves": {"pfx--1091500": False}})
        data = listing(("sgame--Hades II", 5), ("pfx--1145350", 1), ("pfx--1091500", 2), ("game--Mine", 3))
        index = [{"archive": "sgame--Hades II", "appid": "1145350", "name": "Hades II"},
                 {"archive": "game--Mine", "appid": "2931553111", "name": "Mine", "kind": "folder"}]
        asked = []
        with mock.patch.object(cloudgames, "listing", lambda: data), \
             mock.patch.object(gui.drive, "games_index", lambda: index), \
             mock.patch.object(gui.steamnames, "for_archives", lambda a: {"1091500": "Cyberpunk 2077"}), \
             mock.patch.object(gui.steamnames, "covers", lambda ids: asked.append(sorted(ids)) or {"1145350": "https://x.steamstatic.com/c.jpg"}):
            rows = gui.Api().sync_list()["rows"]
        by = {r["id"]: r for r in rows}
        self.assertEqual(len(rows), 3)
        # [v4.8] игру Steam качает Steam — от её строки остаются сохранения
        self.assertEqual((by["sgame--Hades II"]["kind"], by["sgame--Hades II"]["game"]), ("saves", None))
        self.assertEqual((by["sgame--Hades II"]["sync_game"], by["sgame--Hades II"]["sync_saves"]), (None, True))
        self.assertEqual((by["game--Mine"]["sync_game"], by["game--Mine"]["sync_saves"]), (False, None))
        self.assertEqual((by["pfx--1091500"]["title"], by["pfx--1091500"]["sync_saves"]), ("Cyberpunk 2077", False))
        self.assertEqual(by["sgame--Hades II"]["cover"], "https://x.steamstatic.com/c.jpg")
        self.assertEqual(asked, [["1091500", "1145350"]])              # у своей игры (ярлык) обложки нет — не спрашиваем


class TestCovers(unittest.TestCase):
    def test_cover_from_store_answer_cached(self):
        answers = {"3751260": {"success": True, "data": {"name": "Dawnwalker",
                   "capsule_image": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3751260/ab/capsule_231x87.jpg"}},
                   "666": {"success": True, "data": {"name": "Evil", "capsule_image": "https://evil.example/x.jpg"}}}
        got = []

        def get(url, params, timeout):
            got.append(params["appids"])
            return mock.Mock(json=lambda: {params["appids"]: answers[params["appids"]]})
        with mock.patch.object(steamnames.requests, "get", get):
            c = steamnames.covers(["3751260", "666"])
            self.assertEqual(steamnames.names(["3751260"]), {"3751260": "Dawnwalker"})
            steamnames.covers(["3751260"])
        self.assertEqual(c, {"3751260": answers["3751260"]["data"]["capsule_image"]})   # чужой адрес — не берём
        self.assertEqual(sorted(got), ["3751260", "666"])              # по разу: название и обложка — одним вопросом


if __name__ == "__main__":
    unittest.main()

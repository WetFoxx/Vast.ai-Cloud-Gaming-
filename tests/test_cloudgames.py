# cloudgames.remove: какой путь выбирается и что проверяется — с поддельными Drive, Vast и ssh.

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cloudgames  # noqa: E402
from vastapi import VastgameError  # noqa: E402

ARCH = {"pfx--1": {"size": 5, "mtime": "2026-09-23", "files": ["pfx--1.tar.zst", "pfx--1.tar.zst.abcd1234"]},
        "identity": {"size": 1, "mtime": "2026-09-23", "files": ["identity.tar.zst"]}}



def _no_page():
    """Страница wolf (v3.15) в этих тестах «не отвечает» — проверяется путь по SSH (старые машины)."""
    p = mock.patch.object(cloudgames.wolfnode, "post_cmd", lambda *a, **k: None)
    p.start()
    return p


def setUpModule():
    unittest.addModuleCleanup(_no_page().stop)

class TestRemove(unittest.TestCase):
    def setUp(self):
        self.cloud = {k: dict(v) for k, v in ARCH.items()}
        self.trashed, self.ssh = [], []
        for name, value in {"token": lambda: "t", "archives": lambda: dict(self.cloud),
                            "trash": self.fake_trash}.items():
            p = mock.patch.object(cloudgames.drive, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(cloudgames.steamnames, "for_archives", lambda a: {}); p.start(); self.addCleanup(p.stop)

    def fake_trash(self, files):
        self.trashed += files
        self.cloud.pop("pfx--1", None)
        return True

    def machine(self, running, node):
        p1 = mock.patch.object(cloudgames, "list_instances", lambda: [{"id": 1}] if running else [])
        p2 = mock.patch.object(cloudgames, "ts_node", lambda: (True, "100.1.1.1", "n") if node else None)

        def ssh_run(ip, cmd, timeout=180, on_notice=None):
            self.ssh.append(cmd)
            self.cloud.pop("pfx--1", None)
            return 0, ""
        p3 = mock.patch.object(cloudgames, "ssh_run", ssh_run)
        for p in (p1, p2, p3):
            p.start(); self.addCleanup(p.stop)

    def test_listing_separates_service(self):
        d = cloudgames.listing()
        self.assertEqual([g["name"] for g in d["games"]], ["pfx--1"])
        self.assertEqual(d["service"]["names"], ["identity"])

    def test_launcher_game_files_kind(self):
        # [v5.6] файлы игры её лаунчера — своим видом, с номером и названием; в extras — отдельно от модов
        self.cloud["lgame--312060--FINAL FANTASY XIV Online Free Trial"] = {"size": 98 * 10**9, "mtime": "2026-10-09", "files": 40}
        self.cloud["mod--312060--FINAL FANTASY XIV Online Free Trial"] = {"size": 10**6, "mtime": "2026-10-09", "files": 1}
        g = {x["name"]: x for x in cloudgames.listing()["games"]}
        lg = g["lgame--312060--FINAL FANTASY XIV Online Free Trial"]
        self.assertEqual((lg["kind"], lg["appid"], lg["title"]), ("lgame", "312060", "FINAL FANTASY XIV Online Free Trial"))
        x = cloudgames.extras()
        self.assertEqual((list(x["lgame"]), x["lgame"]["312060"]["bytes"]), (["312060"], 98 * 10**9))
        self.assertEqual(list(x["mods"]), ["312060"])
        self.assertEqual(cloudgames.mods(), x["mods"])

    def test_first_run_creates_folder(self):
        made = []
        state = {"arch": None}                        # папки на Drive ещё нет — rclone не может её открыть
        def ensure():
            made.append(1); state["arch"] = {}; return True
        with mock.patch.object(cloudgames.drive, "archives", lambda: state["arch"]), \
             mock.patch.object(cloudgames.drive, "ensure_folder", ensure):
            d = cloudgames.listing()
        self.assertEqual(made, [1])
        self.assertEqual(d["games"], [])

    def test_no_machine_goes_to_trash_with_deltas(self):
        self.machine(running=False, node=False)
        self.assertEqual(cloudgames.remove("pfx--1"), "drive")
        self.assertEqual(self.trashed, ["pfx--1.tar.zst", "pfx--1.tar.zst.abcd1234"])
        self.assertEqual(self.ssh, [])

    def test_running_machine_uses_wolf_forget(self):
        self.machine(running=True, node=True)
        self.assertEqual(cloudgames.remove("pfx--1"), "machine")
        self.assertEqual(self.ssh, ["wolf forget pfx--1"])
        self.assertEqual(self.trashed, [])

    def test_machine_without_tailscale_refuses(self):
        self.machine(running=True, node=False)
        with self.assertRaises(VastgameError) as e:
            cloudgames.remove("pfx--1")
        self.assertEqual(e.exception.key, "games.no_node")
        self.assertEqual((self.trashed, self.ssh), ([], []))

    def test_still_in_cloud_is_an_error(self):
        self.machine(running=False, node=False)
        with mock.patch.object(cloudgames.drive, "trash", lambda files: True):   # «удалил», но файл остался
            with self.assertRaises(VastgameError) as e:
                cloudgames.remove("pfx--1")
        self.assertEqual(e.exception.key, "games.still_there")

    def test_service_archive_cannot_be_removed(self):
        self.machine(running=False, node=False)
        with self.assertRaises(VastgameError):
            cloudgames.remove("identity")
        self.assertEqual(self.trashed, [])


if __name__ == "__main__":
    unittest.main()


class TestGameDataRow(TestRemove):
    """[v5.7] Данные игры (gsave--НОМЕР--Название) — в строке её сохранений, «Убрать» убирает оба архива."""

    def setUp(self):
        super().setUp()
        self.cloud["gsave--1--Hades"] = {"size": 3, "mtime": "2026-10-10", "files": ["gsave--1--Hades.tar.zst"]}
        self.cloud["gsave--7--Celeste"] = {"size": 2, "mtime": "2026-10-09", "files": ["gsave--7--Celeste.tar.zst"]}

    def fake_trash(self, files):
        self.trashed += files
        for n in list(self.cloud):
            if any(f.startswith(n + ".") for f in files):
                self.cloud.pop(n)
        return True

    def test_listing_merges_into_saves_row(self):
        g = {x["name"]: x for x in cloudgames.listing()["games"]}
        self.assertEqual(sorted(g), ["gsave--7--Celeste", "pfx--1"])
        self.assertEqual((g["pfx--1"]["bytes"], g["pfx--1"]["also"], g["pfx--1"]["mtime"]), (8, ["gsave--1--Hades"], "2026-10-10"))
        self.assertEqual(g["pfx--1"]["title"], "Hades")                    # Steam названия не знает — из архива
        c = g["gsave--7--Celeste"]                                        # сохранений Proton нет — своя строка
        self.assertEqual((c["kind"], c["appid"], c["title"]), ("pfx", "7", "Celeste"))

    def test_remove_without_machine_trashes_both(self):
        self.machine(running=False, node=False)
        self.assertEqual(cloudgames.remove("pfx--1"), "drive")
        self.assertEqual(sorted(self.trashed), ["gsave--1--Hades.tar.zst", "pfx--1.tar.zst", "pfx--1.tar.zst.abcd1234"])
        self.assertNotIn("gsave--1--Hades", self.cloud)

    def test_remove_with_machine_forgets_both(self):
        self.machine(running=True, node=True)
        orig = cloudgames.ssh_run

        def ssh_run(ip, cmd, timeout=180, on_notice=None):
            orig(ip, cmd, timeout, on_notice)
            self.cloud.pop(cmd.split(" ", 2)[2], None)
            return 0, ""
        with mock.patch.object(cloudgames, "ssh_run", ssh_run):
            self.assertEqual(cloudgames.remove("pfx--1"), "machine")
        self.assertEqual(self.ssh, ["wolf forget pfx--1", "wolf forget gsave--1--Hades"])

    def test_one_left_is_an_error(self):
        self.machine(running=False, node=False)
        with mock.patch.object(cloudgames.drive, "trash", lambda files: self.cloud.pop("pfx--1") and True):
            with self.assertRaises(VastgameError) as e:
                cloudgames.remove("pfx--1")
        self.assertEqual(e.exception.params.get("name") if hasattr(e.exception, "params") else None, "gsave--1--Hades")


for _n in [n for n in vars(TestRemove) if n.startswith("test_")]:   # унаследованные проверки — только в TestRemove
    setattr(TestGameDataRow, _n, None)


class TestArchiveAttributionAndCatalogMerge(unittest.TestCase):
    """Определение принадлежности архивов играм, объединение метаданных vastgame-games.json и отказ от догадок."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        p_set = mock.patch.object(cloudgames.settings, "FILE", self.tmp / "settings.json")
        p_tok = mock.patch.object(cloudgames.drive, "token", lambda: "t")
        p_names = mock.patch.object(cloudgames.steamnames, "for_archives", lambda a: {})
        for p in (p_set, p_tok, p_names):
            p.start()
            self.addCleanup(p.stop)

    def test_merge_games_index_across_machines_preserves_archives_and_metadata(self):
        # Старый каталог v1 (без поля archives) -> Машина 1 -> Машина 2
        legacy_v1 = [
            {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades", "kind": "steam", "tool": False},
            {"archive": "game--MyGame", "appid": "2400000001", "name": "My Game", "kind": "folder", "tool": False,
             "extra_meta": "v1"},
        ]
        machine_1 = [
            {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades", "kind": "steam", "tool": False,
             "archives": ["sgame--Hades", "pfx--1145350", "gsave--1145350--Hades"]},
        ]
        machine_2 = [
            {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades (Updated)", "kind": "steam", "tool": False,
             "archives": ["sgame--Hades", "mod--1145350--Hades"]},
            {"archive": "game--MyGame", "appid": "2400000001", "name": "", "kind": "folder", "tool": False,
             "archives": ["game--MyGame", "pfx--2400000001"]},
        ]
        merged = {g["archive"]: g for g in cloudgames.merge_games_index(legacy_v1, machine_1, machine_2)}
        self.assertEqual(merged["sgame--Hades"]["name"], "Hades (Updated)")
        self.assertEqual(merged["sgame--Hades"]["archives"],
                         ["gsave--1145350--Hades", "mod--1145350--Hades", "pfx--1145350", "sgame--Hades"])
        # Пустое имя не затирает прежнее, дополнительное поле extra_meta и архивы сохраняются
        self.assertEqual(merged["game--MyGame"]["name"], "My Game")
        self.assertEqual(merged["game--MyGame"]["extra_meta"], "v1")
        self.assertEqual(merged["game--MyGame"]["archives"], ["game--MyGame", "pfx--2400000001"])

    def test_legacy_v1_catalog_attributes_all_archive_kinds(self):
        # Даже если vastgame-games.json старого образца (без поля archives), listing() связывает
        # sgame--, game--, pfx--, gsave--, mod--, lgame-- по однозначному appid
        arch = {
            "sgame--Hades": {"size": 100, "mtime": "2026-10-01", "files": ["sgame--Hades.tar.zst"]},
            "pfx--1145350": {"size": 10, "mtime": "2026-10-02", "files": ["pfx--1145350.tar.zst"]},
            "gsave--1145350--Hades": {"size": 5, "mtime": "2026-10-03", "files": ["gsave--1145350--Hades.tar.zst"]},
            "mod--1145350--Hades": {"size": 20, "mtime": "2026-10-01", "files": ["mod--1145350--Hades.tar.zst"]},
            "lgame--1145350--Hades": {"size": 50, "mtime": "2026-10-01", "files": ["lgame--1145350--Hades.tar.zst"]},
            "game--OwnGame": {"size": 80, "mtime": "2026-10-01", "files": ["game--OwnGame.tar.zst"]},
            "pfx--2500000123": {"size": 4, "mtime": "2026-10-02", "files": ["pfx--2500000123.tar.zst"]},
        }
        legacy_idx = [
            {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades", "kind": "steam", "tool": False},
            {"archive": "game--OwnGame", "appid": "2500000123", "name": "Own Game Title", "kind": "folder", "tool": False},
        ]
        with mock.patch.object(cloudgames.drive, "archives", lambda: arch), \
             mock.patch.object(cloudgames.drive, "games_index", lambda: legacy_idx):
            by_name = {g["name"]: g for g in cloudgames.listing()["games"]}
        self.assertEqual(by_name["sgame--Hades"]["appid"], "1145350")
        self.assertEqual(by_name["sgame--Hades"]["archives"],
                         ["gsave--1145350--Hades", "lgame--1145350--Hades", "mod--1145350--Hades",
                          "pfx--1145350", "sgame--Hades"])
        self.assertEqual(by_name["pfx--1145350"]["game_archive"], "sgame--Hades")
        self.assertEqual(by_name["mod--1145350--Hades"]["game_archive"], "sgame--Hades")
        self.assertEqual(by_name["lgame--1145350--Hades"]["game_archive"], "sgame--Hades")
        # Не-Steam игра и её префикс получают название и связь из индекса
        self.assertEqual(by_name["game--OwnGame"]["appid"], "2500000123")
        self.assertEqual(by_name["game--OwnGame"]["title"], "Own Game Title")
        self.assertEqual(by_name["pfx--2500000123"]["game_archive"], "game--OwnGame")
        self.assertEqual(by_name["pfx--2500000123"]["title"], "Own Game Title")

    def test_prog_shared_across_multiple_games_and_unlinked_prog_not_guessed(self):
        # Одна программа prog--SharedTrainer относится сразу к двум играм,
        # prog--SingleModManager — к одной игре, а prog--Hades (не привязана, хоть и совпадает по имени) — ни к какой.
        cloudgames.settings.set("progs_sel", {"1145350": ["prog:SharedTrainer/t.exe", "prog:SingleModManager/m.exe",
                                                          "pfx:drive_c/something.exe"]})
        cloudgames.settings.set("launch_sel", {"1245620": {"id": "prog:SharedTrainer/t.exe", "args": ""}})
        arch = {
            "sgame--Hades": {"size": 100, "mtime": "2026-10-01", "files": ["sgame--Hades.tar.zst"]},
            "sgame--ELDEN RING": {"size": 200, "mtime": "2026-10-01", "files": ["sgame--ELDEN RING.tar.zst"]},
            "prog--SharedTrainer": {"size": 15, "mtime": "2026-10-01", "files": ["prog--SharedTrainer.tar.zst"]},
            "prog--SingleModManager": {"size": 12, "mtime": "2026-10-01", "files": ["prog--SingleModManager.tar.zst"]},
            "prog--Hades": {"size": 8, "mtime": "2026-10-01", "files": ["prog--Hades.tar.zst"]},
        }
        idx = [
            {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades", "kind": "steam", "tool": False},
            {"archive": "sgame--ELDEN RING", "appid": "1245620", "name": "ELDEN RING", "kind": "steam", "tool": False},
        ]
        with mock.patch.object(cloudgames.drive, "archives", lambda: arch), \
             mock.patch.object(cloudgames.drive, "games_index", lambda: idx):
            by_name = {g["name"]: g for g in cloudgames.listing()["games"]}

        shared = by_name["prog--SharedTrainer"]
        self.assertEqual(shared["appids"], ["1145350", "1245620"])
        self.assertEqual(shared["game_archives"], ["sgame--ELDEN RING", "sgame--Hades"])
        self.assertIsNone(shared["appid"])          # относится к нескольким играм — одиночный appid не выбирается наугад
        self.assertIsNone(shared["game_archive"])

        single = by_name["prog--SingleModManager"]
        self.assertEqual(single["appids"], ["1145350"])
        self.assertEqual(single["appid"], "1145350")
        self.assertEqual(single["game_archives"], ["sgame--Hades"])
        self.assertEqual(single["game_archive"], "sgame--Hades")

        unlinked = by_name["prog--Hades"]
        self.assertEqual(unlinked["appids"], [])
        self.assertIsNone(unlinked["appid"])
        self.assertEqual(unlinked["game_archives"], [])
        self.assertIsNone(unlinked["game_archive"])

    def test_no_speculative_attribution_when_data_insufficient_or_ambiguous(self):
        # 1) Архив игры без записи в vastgame-games.json -> appid=None
        # 2) Две игры с одинаковым appid 777 -> pfx--777 не привязывается ни к одной из них (game_archive=None)
        # 3) Противоречивые названия у соседних архивов mod--888--TitleA и lgame--888--TitleB -> название pfx--888 не угадывается
        arch = {
            "game--UnknownFolder": {"size": 50, "mtime": "2026-10-01", "files": ["game--UnknownFolder.tar.zst"]},
            "pfx--777": {"size": 5, "mtime": "2026-10-01", "files": ["pfx--777.tar.zst"]},
            "pfx--888": {"size": 5, "mtime": "2026-10-01", "files": ["pfx--888.tar.zst"]},
            "mod--888--TitleA": {"size": 2, "mtime": "2026-10-01", "files": ["mod--888--TitleA.tar.zst"]},
            "lgame--888--TitleB": {"size": 3, "mtime": "2026-10-01", "files": ["lgame--888--TitleB.tar.zst"]},
        }
        idx = [
            {"archive": "game--First777", "appid": "777", "name": "First", "kind": "folder", "tool": False},
            {"archive": "game--Second777", "appid": "777", "name": "Second", "kind": "folder", "tool": False},
        ]
        with mock.patch.object(cloudgames.drive, "archives", lambda: arch), \
             mock.patch.object(cloudgames.drive, "games_index", lambda: idx):
            by_name = {g["name"]: g for g in cloudgames.listing()["games"]}

        self.assertIsNone(by_name["game--UnknownFolder"]["appid"])
        self.assertEqual(by_name["game--UnknownFolder"]["appids"], [])
        self.assertIsNone(by_name["pfx--777"]["game_archive"])
        self.assertEqual(by_name["pfx--777"]["game_archives"], [])
        self.assertEqual(by_name["pfx--777"]["title"], "")
        self.assertEqual(by_name["pfx--888"]["title"], "")

    def test_forgotten_primary_archive_not_forced_back_while_children_keep_game_meta(self):
        # Когда основной архив игры (sgame--Hades или game--My_Own) удалён через forget,
        # в vastgame-games.json его больше нет в списке archives, но сама запись игры сохраняется.
        # merge_games_index не возвращает удалённый основной архив обратно в archives,
        # а оставшиеся в облаке pfx--, gsave--, mod--, lgame--, prog-- сохраняют привязку к игре.
        idx = {
            "version": 1,
            "games": [
                {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades",
                 "kind": "steam", "tool": False,
                 "archives": ["pfx--1145350", "gsave--1145350--Hades", "mod--1145350--Hades", "prog--TrainerA"]},
                {"archive": "game--My_Own", "appid": "2500000100", "name": "My Own Game",
                 "kind": "folder", "tool": False,
                 "archives": ["pfx--2500000100"]},
                {"archive": "sgame--FullyRemoved", "appid": "999111", "name": "Fully Removed",
                 "kind": "steam", "tool": False,
                 "archives": []},
            ]
        }
        merged = {g["archive"]: g for g in cloudgames.merge_games_index(idx)}
        self.assertNotIn("sgame--Hades", merged["sgame--Hades"]["archives"])
        self.assertEqual(merged["sgame--Hades"]["archives"],
                         ["gsave--1145350--Hades", "mod--1145350--Hades", "pfx--1145350", "prog--TrainerA"])
        self.assertNotIn("game--My_Own", merged["game--My_Own"]["archives"])
        self.assertEqual(merged["game--My_Own"]["archives"], ["pfx--2500000100"])
        self.assertEqual(merged["sgame--FullyRemoved"]["archives"], [])

        arch = {
            "pfx--1145350": {"size": 10, "mtime": "2026-10-02", "files": ["pfx--1145350.tar.zst"]},
            "gsave--1145350--Hades": {"size": 5, "mtime": "2026-10-03", "files": ["gsave--1145350--Hades.tar.zst"]},
            "mod--1145350--Hades": {"size": 8, "mtime": "2026-10-03", "files": ["mod--1145350--Hades.tar.zst"]},
            "prog--TrainerA": {"size": 12, "mtime": "2026-10-03", "files": ["prog--TrainerA.tar.zst"]},
            "pfx--2500000100": {"size": 4, "mtime": "2026-10-02", "files": ["pfx--2500000100.tar.zst"]},
        }
        with mock.patch.object(cloudgames.drive, "archives", lambda: arch), \
             mock.patch.object(cloudgames.drive, "games_index", lambda: idx):
            by_name = {g["name"]: g for g in cloudgames.listing()["games"]}

        self.assertEqual(by_name["pfx--1145350"]["game_archive"], "sgame--Hades")
        self.assertEqual(by_name["pfx--1145350"]["title"], "Hades")
        self.assertEqual(by_name["pfx--1145350"]["also"], ["gsave--1145350--Hades"])
        self.assertEqual(by_name["mod--1145350--Hades"]["game_archive"], "sgame--Hades")
        self.assertEqual(by_name["prog--TrainerA"]["game_archive"], "sgame--Hades")
        self.assertEqual(by_name["pfx--2500000100"]["game_archive"], "game--My_Own")
        self.assertEqual(by_name["pfx--2500000100"]["title"], "My Own Game")

    def test_forget_propagates_across_catalog_merges_and_machines(self):
        # Регрессионный тест распространения удаления архивов (tombstones / forgotten) между машинами:
        # 1. Исходный каталог содержит sgame--Hades и pfx--1145350.
        # 2. На первой машине пользователь удаляет sgame--Hades через forget ($S/x/sgame--Hades).
        # 3. Формируется обновлённый каталог без sgame--Hades, но с сохранённой записью игры.
        # 4. Объединяются старая и новая версии каталога.
        # 5. Проверяется, что удалённый архив не возвращается, а остальные связанные архивы сохраняются.
        import json
        import subprocess
        from test_game_select import GAMES_PY, Machine

        m1 = Machine()
        m1.steam_game(1145350, "Hades", name="Hades")
        (m1.d / "home/sa/steamapps/compatdata/1145350").mkdir(parents=True)
        ls1 = m1.d / "T/ls"
        ls1.write_text("sgame--Hades.tar.zst\tmd5\t10\npfx--1145350.tar.zst\tmd5\t10\n")
        env1 = {**subprocess.os.environ, "WOLF_STATE": str(m1.d / "S"), "WOLF_LS": str(ls1), "WOLF_HOME": str(m1.d / "home")}

        # Шаг 1: формируем исходный каталог с sgame--Hades и pfx--1145350
        r_before = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m1.d / "home/sa/steamapps")],
                                  input="", capture_output=True, text=True, env=env1)
        self.assertEqual(r_before.returncode, 0, r_before.stderr)
        cat_before = json.loads(r_before.stdout)
        idx_before_path = m1.d / "S/index_before.json"
        idx_before_path.write_text(r_before.stdout)
        self.assertEqual(cat_before["games"][0]["archives"], ["pfx--1145350", "sgame--Hades"])

        # Шаги 2 и 3: пользователь удаляет sgame--Hades через forget -> формируется обновлённый каталог
        (m1.d / "S/x").mkdir(parents=True, exist_ok=True)
        (m1.d / "S/x/sgame--Hades").touch()
        ls1.write_text("pfx--1145350.tar.zst\tmd5\t10\n")
        r_after = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m1.d / "home/sa/steamapps"), str(idx_before_path)],
                                 input="", capture_output=True, text=True, env=env1)
        self.assertEqual(r_after.returncode, 0, r_after.stderr)
        cat_after = json.loads(r_after.stdout)
        self.assertEqual(cat_after["games"][0]["archive"], "sgame--Hades")
        self.assertEqual(cat_after["games"][0]["archives"], ["pfx--1145350"])
        self.assertEqual(cat_after["games"][0]["forgotten"], ["sgame--Hades"])

        # Шаги 4 и 5: объединяем старую и новую версии каталога (в обоих порядках)
        merged_12 = {g["archive"]: g for g in cloudgames.merge_games_index(cat_before, cat_after)}
        self.assertEqual(merged_12["sgame--Hades"]["archives"], ["pfx--1145350"])
        self.assertNotIn("sgame--Hades", merged_12["sgame--Hades"]["archives"])
        self.assertEqual(merged_12["sgame--Hades"]["forgotten"], ["sgame--Hades"])

        merged_21 = {g["archive"]: g for g in cloudgames.merge_games_index(cat_after, cat_before)}
        self.assertEqual(merged_21["sgame--Hades"]["archives"], ["pfx--1145350"])
        self.assertNotIn("sgame--Hades", merged_21["sgame--Hades"]["archives"])

        # Одновременное накопление новых архивов на другой машине и сохранение удалений (включая дочерние архивы)
        cat_machine_2 = {
            "version": 1,
            "games": [
                {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades",
                 "kind": "steam", "tool": False,
                 "archives": ["sgame--Hades", "pfx--1145350", "mod--1145350--Hades", "gsave--1145350--Hades"],
                 "forgotten": ["lgame--1145350--Hades"]}
            ]
        }
        cat_with_lgame = {
            "version": 1,
            "games": [
                {"archive": "sgame--Hades", "appid": "1145350", "name": "Hades",
                 "kind": "steam", "tool": False,
                 "archives": ["sgame--Hades", "lgame--1145350--Hades", "prog--TrainerA"]}
            ]
        }
        merged_all = {g["archive"]: g for g in cloudgames.merge_games_index(cat_before, cat_with_lgame, cat_after, cat_machine_2)}
        self.assertEqual(merged_all["sgame--Hades"]["archives"],
                         ["gsave--1145350--Hades", "mod--1145350--Hades", "pfx--1145350", "prog--TrainerA"])
        self.assertEqual(merged_all["sgame--Hades"]["forgotten"],
                         ["lgame--1145350--Hades", "sgame--Hades"])

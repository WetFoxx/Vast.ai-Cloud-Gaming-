# wolf-setup.sh v5.6: рецепт «русский перевод FF14» (wolf-recipes.py ff14-ru) — настоящий код из heredoc на поддельном
# «диске C:»; интернет (список плагинов SeaOfStars, Penumbra, релиз xivrus на GitHub) — подделка fetch. Первая
# установка с нуля, поверх настроек как у Алексея (свой выбор не теряется), повтор без изменений, новая версия
# перевода, нет связи с GitHub, архив с путём наружу; выбор из VG_SYNC и порядок в wolf — функциями wolf.

import base64
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_game_select import TMP, WOLF, Machine, fn, heredoc  # noqa: E402

PY = TMP / "wolf-recipes.py"
PY.write_text(heredoc("wolf-recipes.py", "RECIPES"))
spec = importlib.util.spec_from_file_location("wolf_recipes", PY)
wr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wr)

ROAM = "users/steamuser/AppData/Roaming/XIVLauncher"
DIR = "FINAL FANTASY XIV Online"


def zbytes(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return b.getvalue()


def pmp(version, extra=None):
    return zbytes({"meta.json": json.dumps({"FileVersion": 3, "Name": "XIV Rus", "Version": version,
                                            "Website": "https://xivrus.ru"}),
                   "default_mod.json": "{}",
                   "group_001_axis font.json": json.dumps({"Name": "AXIS Font", "Type": "Single", "DefaultSettings": 0}),
                   "group_002_settings.json": json.dumps({"Name": "Settings", "Type": "Multi", "DefaultSettings": 255}),
                   f"ui/v{version}.tex": "x", **(extra or {})})


class Net:
    """Поддельный интернет: что лежит по адресам и сколько раз их спрашивали."""

    def __init__(self, version="1.19.1"):
        self.version, self.calls, self.down = version, [], set()
        self.penumbra = zbytes({"Penumbra.dll": "dll", "Penumbra.json": '{"Name": "Penumbra"}'})

    def __call__(self, url, dest=None, timeout=60):
        self.calls.append(url)
        if any(h in url for h in self.down):
            raise OSError("нет связи")
        if url == wr.SEA:
            data = json.dumps([{"InternalName": "Other"},
                               {"InternalName": "Penumbra", "Name": "Penumbra", "AssemblyVersion": "1.7.2.1",
                                "DalamudApiLevel": 15, "RepoUrl": "https://github.com/xivdev/Penumbra",
                                "DownloadLinkInstall": "https://x/Penumbra.zip", "LoadPriority": 69420,
                                "DownloadCount": 5, "IsHide": "False"}]).encode()
        elif url == "https://x/Penumbra.zip":
            data = self.penumbra
        elif url == wr.XIVRUS:
            data = json.dumps({"tag_name": "v" + self.version, "assets": [
                {"name": "release.pmp", "browser_download_url": "https://x/release.pmp"}]}).encode()
        elif url == "https://x/release.pmp":
            data = pmp(self.version)
        else:
            raise AssertionError(url)
        if dest is None:
            return data
        Path(dest).write_bytes(data)
        return dest


class Recipe(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        self.c = self.d / "drive_c"
        self.cache = self.d / "cache"
        self.c.mkdir()
        self.net = Net()

    def run_recipe(self, appid="312060"):
        with mock.patch.object(wr, "fetch", self.net), mock.patch("sys.stdout", io.StringIO()) as out:
            code = wr.ff14_ru(str(self.c), appid, DIR, str(self.cache))
        return code, out.getvalue()

    def j(self, rel):
        return json.loads((self.c / rel).read_text(encoding="utf-8-sig"))

    def collection(self):
        act = self.j(f"{ROAM}/pluginConfigs/Penumbra/active_collections.json")
        return self.j(f"{ROAM}/pluginConfigs/Penumbra/collections/{act['Default']}.json")


class TestFreshInstall(Recipe):
    def test_everything_from_scratch(self):
        code, out = self.run_recipe()
        self.assertEqual(code, 0)
        lc = self.j(f"{ROAM}/launcherConfigV3.json")
        self.assertEqual((lc["GamePath"], lc["IsFt"], lc["Language"], lc["InGameAddonEnabled"]),
                         ("Z:\\home\\user\\SteamGames\\" + DIR, "true", "English", "true"))
        self.assertTrue(all(isinstance(v, str) for v in lc.values()))          # XIVLauncher хранит всё строками
        dc = self.j(f"{ROAM}/dalamudConfig.json")
        self.assertEqual([(r["Url"], r["IsEnabled"]) for r in dc["ThirdRepoList"]["$values"]], [(wr.SEA, True)])
        self.assertTrue(dc["ThirdRepoSpeedbumpDismissed"] and dc["IsResumeGameAfterPluginLoad"])
        prof = dc["DefaultProfile"]["Plugins"]["$values"]
        man = self.j(f"{ROAM}/installedPlugins/Penumbra/1.7.2.1/Penumbra.json")
        self.assertTrue((self.c / ROAM / "installedPlugins/Penumbra/1.7.2.1/Penumbra.dll").exists())
        self.assertEqual((man["InstalledFromUrl"], man["IsThirdParty"], man["Disabled"], man["InternalName"]),
                         (wr.SEA, True, False, "Penumbra"))
        self.assertIsNone(man["DownloadLinkInstall"])
        self.assertNotIn("DownloadCount", man)
        self.assertEqual(prof, [{"$type": wr.T_PLUGIN, "InternalName": "Penumbra",
                                 "WorkingPluginId": man["WorkingPluginId"], "IsEnabled": True}])
        pc = self.j(f"{ROAM}/pluginConfigs/Penumbra/config/penumbra.json")
        self.assertEqual(pc["ModDirectory"], "C:\\FFXIVMods")
        self.assertEqual(self.j("FFXIVMods/XIV Rus/meta.json")["Version"], "1.19.1")
        self.assertEqual(self.collection()["Settings"],
                         {"XIV Rus": {"Settings": {"AXIS Font": 0, "Settings": 255}, "Priority": 0, "Enabled": True}})
        self.assertIn("перевод 1.19.1, включён", out)
        self.assertEqual(list(self.cache.iterdir()), [])                         # скачанное убрано

    def test_full_version_is_not_trial(self):
        self.run_recipe("39210")
        self.assertEqual(self.j(f"{ROAM}/launcherConfigV3.json")["IsFt"], "false")

    def test_other_game_refused(self):
        self.assertEqual(self.run_recipe("570")[0], 2)
        self.assertFalse((self.c / ROAM).exists())


class TestExisting(Recipe):
    def setUp(self):
        super().setUp()
        self.run_recipe()
        self.net.calls.clear()

    def test_second_time_only_checks_version(self):
        (self.c / "FFXIVMods/XIV Rus/ui/v1.19.1.tex").write_text("мой")
        code, out = self.run_recipe()
        self.assertIn("перевод 1.19.1 — последний", out)
        self.assertIn("Penumbra уже стоит", out)
        self.assertEqual(self.net.calls, [wr.XIVRUS])                             # только номер версии
        self.assertEqual((self.c / "FFXIVMods/XIV Rus/ui/v1.19.1.tex").read_text(), "мой")

    def test_new_translation_replaces_old_and_keeps_choice(self):
        col = self.collection()
        col["Settings"]["XIV Rus"]["Settings"]["AXIS Font"] = 1                   # человек выбрал другой шрифт
        act = self.j(f"{ROAM}/pluginConfigs/Penumbra/active_collections.json")
        (self.c / f"{ROAM}/pluginConfigs/Penumbra/collections/{act['Default']}.json").write_text(json.dumps(col))
        self.net.version = "1.20.0"
        code, out = self.run_recipe()
        self.assertIn("перевод 1.19.1 → 1.20.0", out)
        self.assertFalse((self.c / "FFXIVMods/XIV Rus/ui/v1.19.1.tex").exists())  # старая удалена целиком
        self.assertTrue((self.c / "FFXIVMods/XIV Rus/ui/v1.20.0.tex").exists())
        self.assertEqual(self.collection()["Settings"]["XIV Rus"]["Settings"]["AXIS Font"], 1)

    def test_old_under_other_name_removed(self):
        # человек импортировал перевод сам — папка названа иначе; найти её по сайту xivrus и убрать
        (self.c / "FFXIVMods/XIV Rus").rename(self.c / "FFXIVMods/XIVRus-1.19.1")
        self.net.version = "1.20.0"
        self.run_recipe()
        self.assertEqual(sorted(x.name for x in (self.c / "FFXIVMods").iterdir()), ["XIV Rus"])

    def test_no_github_keeps_translation(self):
        self.net.down.add("api.github.com")
        code, out = self.run_recipe()
        self.assertEqual(code, 0)
        self.assertIn("перевод не обновлён", out)
        self.assertTrue((self.c / "FFXIVMods/XIV Rus/meta.json").exists())


class TestAlexeysSetup(Recipe):
    def test_own_settings_kept(self):
        # Как у Алексея после ручной установки: свой аккаунт, автовход, путь в Penumbra, Penumbra уже стоит
        roam = self.c / ROAM
        (roam / "installedPlugins/Penumbra/1.7.2.1").mkdir(parents=True)
        (roam / "installedPlugins/Penumbra/1.7.2.1/Penumbra.dll").write_text("dll")
        (roam / "installedPlugins/Penumbra/1.7.2.1/Penumbra.json").write_text(json.dumps({"WorkingPluginId": "abc"}))
        (roam / "launcherConfigV3.json").write_text(json.dumps({"CurrentAccountId": "acc", "AutologinEnabled": "true",
                                                                "IsFt": "true", "Language": "Japanese"}))
        (roam / "dalamudConfig.json").write_text(json.dumps({
            "$type": "x", "LogLevel": 2,
            "ThirdRepoList": {"$type": wr.T_REPOS, "$values": [{"$type": wr.T_REPO, "Url": "https://other/repo.json",
                                                                 "IsEnabled": True},
                                                                {"$type": wr.T_REPO, "Url": wr.SEA, "IsEnabled": False}]},
            "DefaultProfile": {"$type": wr.T_PROFILE, "Plugins": {"$type": wr.T_PLUGINS, "$values": [
                {"$type": wr.T_PLUGIN, "InternalName": "Penumbra", "WorkingPluginId": "abc", "IsEnabled": False}]}}}))
        (roam / "pluginConfigs/Penumbra/config").mkdir(parents=True)
        (roam / "pluginConfigs/Penumbra/config/penumbra.json").write_text(json.dumps({"Version": 100,
                                                                                     "ModDirectory": "C:\\MyMods"}))
        self.run_recipe()
        lc = self.j(f"{ROAM}/launcherConfigV3.json")
        self.assertEqual((lc["CurrentAccountId"], lc["AutologinEnabled"], lc["Language"]), ("acc", "true", "English"))
        dc = self.j(f"{ROAM}/dalamudConfig.json")
        self.assertEqual(dc["LogLevel"], 2)
        self.assertEqual([(r["Url"], r["IsEnabled"]) for r in dc["ThirdRepoList"]["$values"]],
                         [("https://other/repo.json", True), (wr.SEA, True)])            # без дубля, включён
        self.assertEqual(dc["DefaultProfile"]["Plugins"]["$values"][0]["IsEnabled"], False)   # выключил сам — его дело
        self.assertNotIn("https://x/Penumbra.zip", self.net.calls)
        self.assertTrue((self.c / "MyMods/XIV Rus/meta.json").exists())                 # в его папку модов
        self.assertFalse((self.c / "FFXIVMods").exists())


class TestSafety(Recipe):
    def test_path_outside_refused(self):
        self.net.version = "9.9"
        bad = pmp("9.9", {"../../evil.txt": "x"})
        real = self.net.__call__
        self.net = lambda url, dest=None, timeout=60: (Path(dest).write_bytes(bad) and dest) \
            if url == "https://x/release.pmp" else real(url, dest, timeout)
        code, out = self.run_recipe()
        self.assertIn("перевод не обновлён (ValueError", out)
        self.assertFalse((self.d / "evil.txt").exists())
        self.assertFalse((self.c / "FFXIVMods/XIV Rus").exists())

    def test_mark_xivlauncher(self):
        self.assertEqual(wr.mark(str(self.c)), 1)                                     # ещё не стоит
        (self.c / wr.XIVL_EXE).parent.mkdir(parents=True)
        (self.c / wr.XIVL_EXE).write_text("x")
        (self.c / ".vastgame-installed.json").write_text(json.dumps({"exes": ["a/b.exe"]}))
        wr.mark(str(self.c))
        self.assertEqual(self.j(".vastgame-installed.json")["exes"], ["a/b.exe", wr.XIVL_EXE])


class TestWolfSide(unittest.TestCase):
    def test_vg_sync_recipes(self):
        m = Machine()
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store",
                                          "recipes": {"312060": ["ff14-ru", "evil"], "x": ["ff14-ru"], "39210": "ff14-ru",
                                                      "1": ["ff14-ru"]}}).encode()).decode()
        m.run("sel_init", env=f'VG_SYNC="{vg}"')
        self.assertEqual((m.d / "S/sel.recipe.ff14-ru").read_text(), "312060\n1\n")
        self.assertFalse((m.d / "S/sel.recipe.evil").exists())

    def test_order_in_play(self):
        pw = fn("play_watch")
        self.assertLess(pw.index("mods_apply"), pw.index("recipe_play"))
        self.assertLess(pw.index("recipe_play"), pw.index("play_launch"))
        rp = fn("recipe_play")
        self.assertIn("inlist \"$a\" sel.recipe.ff14-ru || return 0", rp)
        order = [rp.index(x) for x in ("pfx_make", "runtimes", "wolf-recipes.py ff14-ru ", "XIVLauncher-win-Setup",
                                       "ff14-ru-mark", "launch_opts")]
        self.assertEqual(order, sorted(order))                 # настройки — до первого запуска XIVLauncher
        self.assertIn('"--silent"', rp)
        self.assertIn("launch_opts", fn("runtimes"))             # прокладка — до запуска через Steam
        self.assertIn("XIVL_SETUP=\"https://github.com/goatcorp/", WOLF)


if __name__ == "__main__":
    unittest.main()

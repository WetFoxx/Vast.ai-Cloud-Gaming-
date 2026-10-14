# wolf-setup.sh v5.7: своя игра (папка Games с паспортом .vastgame.json и своим «диском C:» .prefix) — ярлык Steam на
# запускатель vg-run (без Proton у ярлыка), запуск vg-run через вход Steam Linux Runtime и Proton, как делает Steam
# (разведка 2026-10-09). Настоящий код из heredoc; Steam, Proton и Runtime — поддельные папки и скрипты.

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_game_select import TMP, WOLF, fn, heredoc  # noqa: E402
from test_shortcuts import Base  # noqa: E402

VGRUN = TMP / "vg-run"
VGRUN.write_text(heredoc("vg-run", "VGRUN"))
VGRUN.chmod(0o755)


def own(folder, exe, args=""):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".vastgame.json").write_text(json.dumps({"exe": exe, "args": args}))


class TestOwnShortcuts(Base):
    def setUp(self):
        super().setUp()
        self.mine = self.games / "Genshin"
        self.game("Genshin", {".prefix/pfx/drive_c/Program Files/HoYoPlay/launcher.exe": 10})
        own(self.mine, ".prefix/pfx/drive_c/Program Files/HoYoPlay/launcher.exe")

    def test_own_game_points_to_vg_run_without_proton(self):
        self.assertEqual(self.run_it(), 3)
        sc = {e["AppName"]: e for e in self.shortcuts()}
        g = sc["Genshin"]
        self.assertEqual((g["Exe"], g["LaunchOptions"], g["StartDir"]),
                         ('"/usr/local/bin/vg-run"', f'"{self.mine}"', f'"{self.mine}/"'))
        aid = g["appid"][1] & 0xFFFFFFFF
        self.assertEqual(aid, self.ws.appid('"/usr/local/bin/vg-run"', "Genshin"))
        cfg = (self.steam / "config/config.vdf").read_text()
        self.assertNotIn(f'"{aid}"', cfg)                                    # Proton включает vg-run, не Steam
        self.assertIn("The Blood of Dawnwalker", sc)                          # обычная игра из Games — как раньше
        self.assertEqual(self.run_it(), 0)                                    # второй раз — ничего

    def test_becomes_own_and_removed(self):
        # portable-игра без паспорта (как до v5.7) — ярлык прямо на exe; получила паспорт — ярлык на vg-run, без дубля
        self.game("Hades", {"Hades.exe": 50})
        self.run_it()
        self.assertIn("Hades.exe", self.shortcuts_by()["Hades"]["Exe"])
        own(self.games / "Hades", "Hades.exe")
        self.run_it()
        names = [e["AppName"] for e in self.shortcuts()]
        self.assertEqual(names.count("Hades"), 1)
        self.assertEqual(self.shortcuts_by()["Hades"]["Exe"], '"/usr/local/bin/vg-run"')
        import shutil
        shutil.rmtree(self.games / "Hades")                                   # папку удалили — ярлык убран
        self.run_it()
        self.assertNotIn("Hades", self.shortcuts_by())

    def test_bad_manifest_ignored(self):
        own(self.mine, "../../etc/passwd")
        self.assertFalse(self.ws.own_game(str(self.mine)))
        own(self.mine, "/etc/passwd")
        self.assertFalse(self.ws.own_game(str(self.mine)))

    def test_index_appid(self):
        out = subprocess.run([sys.executable, str(self.script), str(self.games), str(self.steam), "--index"],
                             capture_output=True, text=True).stdout
        line = next(x for x in out.splitlines() if x.startswith("game--Genshin"))
        self.assertEqual(line.split("\t")[1], str(self.ws.appid('"/usr/local/bin/vg-run"', "Genshin")))

    def shortcuts_by(self):
        return {e["AppName"]: e for e in self.shortcuts()}


class TestVgRun(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        self.home = self.d / "home"
        self.root = self.home / ".steam/debian-installation"
        common = self.root / "steamapps/common"
        pr = common / "Proton - Experimental"
        pr.mkdir(parents=True)
        (pr / "toolmanifest.vdf").write_text('"manifest"\n{\n  "commandline" "/proton %verb%"\n'
                                             '  "require_tool_appid" "4183110"\n}\n')
        (pr / "proton").write_text("#!/bin/sh\n")
        (pr / "proton").chmod(0o755)
        (self.root / "steamapps/appmanifest_4183110.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"4183110"\n\t"installdir"\t\t"SteamLinuxRuntime_4"\n}\n')
        slr = common / "SteamLinuxRuntime_4"
        slr.mkdir()
        (slr / "_v2-entry-point").write_text('#!/bin/sh\necho "cwd=$(pwd)"\necho "pfx=$STEAM_COMPAT_DATA_PATH"\n'
                                             'echo "client=$STEAM_COMPAT_CLIENT_INSTALL_PATH"\n'
                                             'for a in "$@"; do echo "arg=$a"; done\n')
        (slr / "_v2-entry-point").chmod(0o755)
        self.game = self.home / "Downloads/Games/Genshin"
        self.home.mkdir(parents=True, exist_ok=True)
        self.home = self.home.resolve()                    # на Mac временные папки — за ссылкой /var → /private/var
        self.root = self.root.resolve()
        self.game = self.home / "Downloads/Games/Genshin"
        exe = self.game / ".prefix/pfx/drive_c/Program Files/HoYoPlay/launcher.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        self.exe = exe.resolve()

    def run_vg(self, *args):
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"]}
        r = subprocess.run([sys.executable, str(VGRUN), *args], capture_output=True, text=True, env=env, timeout=20)
        return r.returncode, r.stdout.splitlines(), r.stderr

    def test_game_runs_in_its_own_c_drive(self):
        own(self.game, ".prefix/pfx/drive_c/Program Files/HoYoPlay/launcher.exe", "--lang ru")
        code, out, err = self.run_vg(str(self.game))
        self.assertEqual(code, 0, err)
        self.assertIn(f"pfx={self.game}/.prefix", out)
        self.assertIn(f"client={self.root}", out)
        self.assertIn(f"cwd={self.exe.parent}", out)
        args = [x[4:] for x in out if x.startswith("arg=")]
        pr = str(self.root / "steamapps/common/Proton - Experimental/proton")
        self.assertEqual(args, ["--verb=waitforexitandrun", "--", pr, "waitforexitandrun", str(self.exe),
                                "--lang", "ru"])

    def test_installer_in_the_same_c_drive(self):
        setup = self.home / "Downloads/HoYoPlay_setup.exe"
        setup.write_text("x")
        code, out, err = self.run_vg(str(self.game), "--exe", str(setup), "/S")
        self.assertEqual(code, 0, err)
        self.assertIn(f"pfx={self.game}/.prefix", out)
        self.assertEqual([x[4:] for x in out if x.startswith("arg=")][-2:], [str(setup), "/S"])

    def test_refuses_bad_manifest(self):
        own(self.game, "../../../etc/passwd")
        code, out, err = self.run_vg(str(self.game))
        self.assertEqual(code, 1)
        self.assertIn("паспорт", err)
        self.assertEqual(out, [])
        outside = self.d / "outside"
        outside.mkdir()
        (outside / "evil.exe").write_text("x")
        (self.game / "link").symlink_to(outside)                             # ссылка из папки игры наружу
        own(self.game, "link/evil.exe")
        self.assertEqual(self.run_vg(str(self.game))[0], 1)


class TestWolfSide(unittest.TestCase):
    def test_prefix_temp_not_archived(self):
        self.assertIn("game--*)    F \"$A\" \\( -ipath '*/drive_c/users/*/AppData/Local/Temp'", WOLF)

    def test_games_being_added_are_hidden(self):
        # [живой случай 2026-10-09] посреди установки 7-Zip машина спросила «новая игра — в облако?»: папка уже есть,
        # паспорта ещё нет. Пока помощник жив (его номер в метке) — папки нет ни для вопросов, ни для выгрузки
        h = Path(tempfile.mkdtemp(dir=TMP))
        gd, marks = h / "Games", h / ".local/share/vastgame/adding"
        for n in ("Old", "Adding", "Crashed", "Unpack.vgtmp"):
            (gd / n).mkdir(parents=True)
        marks.mkdir(parents=True)
        dead = subprocess.run(["sh", "-c", "echo $$"], capture_output=True, text=True).stdout.strip()
        (marks / "Adding").write_text(str(os.getpid()))
        (marks / "Crashed").write_text(dead)                             # помощник упал — метка не мешает
        script = f'''
DH="{h}"; GD="{gd}"
ld() {{ for d in "$1"/*/; do d=${{d%/}}; echo "$2${{d##*/}}"; done; }}
{fn("ldg")}
ldg
'''
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(out), ["game--Crashed", "game--Old"])
        self.assertIn("ldg; }", fn("ondisk"))
        self.assertIn("{ ldg;", fn("gsync"))
        self.assertEqual(WOLF.count('ld "$GD"'), 1)                     # только внутри ldg

    def test_own_game_exit_no_empty_saves_push(self):
        # [живой случай 2026-10-09] своя игра из Steam: выход → «выгружаю pfx--<номер ярлыка>», а «диска C:» от Steam нет
        self.assertIn('wants "pfx--$a" && [ -d "$DH/$SR/steamapps/compatdata/$a" ] && arr+=("pfx--$a")', fn("watch_games"))



# ---------------------------------------------------------------- помощник двойного щелчка (vg-open)
import importlib.util  # noqa: E402

VGOPEN = TMP / "vg-open.py"
VGOPEN.write_text(heredoc("vg-open", "VGOPEN"))


def load_vgopen(home):
    os.environ["HOME"] = str(home)
    os.environ.pop("VG_GAMES", None)
    spec = importlib.util.spec_from_file_location(f"vgopen_{id(home)}", VGOPEN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class OpenBase(unittest.TestCase):
    def setUp(self):
        self.old_home = os.environ.get("HOME")
        self.home = Path(tempfile.mkdtemp(dir=TMP)).resolve()
        self.vo = load_vgopen(self.home)
        self.gd = self.home / "Downloads/Games"
        self.gd.mkdir(parents=True)

    def tearDown(self):
        os.environ["HOME"] = self.old_home

    def f(self, rel, size=10):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p


class TestOpenDecisions(OpenBase):
    def test_kind(self):
        self.assertEqual(self.vo.kind(str(self.f("Downloads/Genshin_Impact_setup.exe"))), "installer")
        self.assertEqual(self.vo.kind(str(self.f("Downloads/game.msi"))), "installer")
        self.assertEqual(self.vo.kind(str(self.f("Downloads/Hollow.zip"))), "archive")
        self.assertEqual(self.vo.kind(str(self.f("Downloads/Hollow Knight/hollow_knight.exe"))), "ask")
        self.assertEqual(self.vo.kind(str(self.f("Downloads/Games/Mine/tool.exe"))), "inside")
        self.assertIsNone(self.vo.game_of(str(self.f("Downloads/Games/.hidden/x.exe"))))

    def test_clean_name(self):
        c = self.vo.clean_name
        self.assertEqual(c("Genshin_Impact_setup_2.3.1.exe"), "Genshin Impact")
        self.assertEqual(c("HoYoPlay-installer-x64.exe"), "HoYoPlay")
        self.assertEqual(c('bad/na:me*?.zip'), "badname")
        self.assertEqual(c("setup.exe"), "Game")

    def test_exes_skip_windows_and_helpers_known_first(self):
        g = self.gd / "Genshin"
        drive = ".prefix/pfx/drive_c/"
        for rel, size in ((drive + "windows/system32/notepad.exe", 9), (drive + "Program Files/Common Files/x.exe", 9),
                          (drive + "Program Files/HoYoPlay/launcher.exe", 5),
                          (drive + "Program Files/HoYoPlay/unins000.exe", 99),
                          (drive + "Program Files/HoYoPlay/games/Genshin Impact Game/GenshinImpact.exe", 900),
                          (drive + "users/steamuser/AppData/Local/Temp/is-1/setup.tmp.exe", 50)):
            self.f(str((g / rel).relative_to(self.home)), size)
        self.assertEqual(self.vo.exes(str(g)), [drive + "Program Files/HoYoPlay/launcher.exe",
                                                drive + "Program Files/HoYoPlay/games/Genshin Impact Game/GenshinImpact.exe"])
        before = set(self.vo.exes(str(g)))
        self.f(str((g / (drive + "Program Files/Mod/ModTool.exe")).relative_to(self.home)))
        self.assertEqual(self.vo.exes(str(g), before), [drive + "Program Files/Mod/ModTool.exe"])   # только новое

    def test_portable_plan(self):
        lone = self.f("Downloads/Celeste.exe")
        self.assertEqual(self.vo.portable_plan(str(lone)), (str(lone), "Celeste", "Celeste.exe"))   # не вся папка «Загрузки»
        exe = self.f("Downloads/Hollow Knight v1.5/bin/hollow_knight.exe")
        self.assertEqual(self.vo.portable_plan(str(exe)),
                         (str(self.home / "Downloads/Hollow Knight v1.5/bin"), "bin", "hollow_knight.exe"))

    def test_unpack_flattens_and_refuses_escape(self):
        import zipfile
        z = self.home / "Downloads/Hollow.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("Hollow Knight/hollow_knight.exe", "x")
            zf.writestr("Hollow Knight/data/a.dat", "y")
        self.vo.unpack(str(z), str(self.gd / "Hollow"))
        self.assertTrue((self.gd / "Hollow/hollow_knight.exe").exists())     # общая папка сверху — поднята
        bad = self.home / "Downloads/bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("../../evil.exe", "x")
        with self.assertRaises(ValueError):
            self.vo.unpack(str(bad), str(self.gd / "Bad"))
        self.assertFalse((self.home / "evil.exe").exists())


def pe(gui=True, product=None):
    """Маленький «файл Windows»: заголовок PE с подсистемой (2 — окно, 3 — консоль) и, если нужно, «Название продукта»."""
    head = bytearray(512)
    head[:2] = b"MZ"
    head[0x3C:0x40] = (0x80).to_bytes(4, "little")
    head[0x80:0x84] = b"PE\0\0"
    sub = 0x80 + 24 + 68
    head[sub:sub + 2] = (2 if gui else 3).to_bytes(2, "little")
    tail = b""
    if product:
        tail = "ProductName".encode("utf-16-le") + b"\0\0" + b"\0\0" + product.encode("utf-16-le") + b"\0\0"
    return bytes(head) + tail


def lnk(target):
    """Ярлык Windows, как его пишет Wine: заголовок 0x4C и путь к программе обычным текстом (LinkInfo)."""
    return b"\x4c\0\0\0" + b"\0" * 72 + target.encode("latin-1") + b"\0" + "Описание".encode("utf-16-le")


class TestWindowsInfo(OpenBase):
    def test_gui_console_product_and_shortcut(self):
        g = self.f("Downloads/x/gui.exe")
        g.write_bytes(pe(True, "7-Zip"))
        c = self.f("Downloads/x/con.exe")
        c.write_bytes(pe(False))
        self.assertTrue(self.vo.pe_gui(str(g)))
        self.assertFalse(self.vo.pe_gui(str(c)))
        self.assertIsNone(self.vo.pe_gui(str(self.f("Downloads/x/text.exe"))))
        self.assertEqual(self.vo.product_name(str(g)), "7-Zip")
        self.assertEqual(self.vo.default_name(str(g)), "7-Zip")
        self.assertEqual(self.vo.default_name(str(c)), "con")                    # нет сведений — из имени файла
        l = self.f("Downloads/x/a.lnk")
        l.write_bytes(lnk("C:\\Program Files\\7-Zip\\7zFM.exe"))
        self.assertEqual(self.vo.lnk_target(str(l)), "C:\\Program Files\\7-Zip\\7zFM.exe")

    def game_with_7zip(self):
        g = self.gd / "7-Zip"
        drive = g / ".prefix/pfx/drive_c"
        for name, gui in (("7zFM.exe", True), ("7zG.exe", True), ("7z.exe", False)):
            p = drive / "Program Files/7-Zip" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(pe(gui))
        return g, drive

    def test_shortcut_decides_without_question(self):
        g, drive = self.game_with_7zip()
        menu = drive / "ProgramData/Microsoft/Windows/Start Menu/Programs/7-Zip"
        menu.mkdir(parents=True)
        (menu / "7-Zip File Manager.lnk").write_bytes(lnk("C:\\Program Files\\7-Zip\\7zFM.exe"))
        (menu / "7-Zip Help.lnk").write_bytes(lnk("C:\\Program Files\\7-Zip\\7zG.exe"))   # «справка» — мимо
        self.assertEqual(self.vo.candidates(str(g)),
                         [(".prefix/pfx/drive_c/Program Files/7-Zip/7zFM.exe", "7-Zip File Manager")])

    def test_without_shortcuts_console_tools_dropped(self):
        g, drive = self.game_with_7zip()
        got = self.vo.candidates(str(g))
        self.assertEqual(sorted(r for r, _ in got), [".prefix/pfx/drive_c/Program Files/7-Zip/7zFM.exe",
                                                     ".prefix/pfx/drive_c/Program Files/7-Zip/7zG.exe"])
        self.assertIn("7zFM.exe — C:\\Program Files\\7-Zip", dict(got).values())


class TestOpenFlows(OpenBase):
    """Сценарии целиком: zenity и vg-run — поддельные (ответы по очереди, вызовы — в журнал)."""

    def setUp(self):
        super().setUp()
        bin_ = self.home / "bin"
        bin_.mkdir()
        self.answers = self.home / "answers"
        self.calls = self.home / "calls"
        (bin_ / "zenity").write_text('#!/bin/sh\necho "zenity $*" >> "$VG_CALLS"\n'
                                     'line=$(head -1 "$VG_ANS"); sed -i.b 1d "$VG_ANS"\n'
                                     'code=${line%%|*}; out=${line#*|}; [ -n "$out" ] && echo "$out"; exit "$code"\n')
        (bin_ / "zenity").chmod(0o755)
        self.vgrun = bin_ / "vg-run"
        self.vgrun.write_text('#!/bin/sh\necho "vg-run $*" >> "$VG_CALLS"\n'
                              'm="$HOME/.local/share/vastgame/adding/${1##*/}"; [ -e "$m" ] && echo "mark $(cat "$m")" >> "$VG_CALLS"\n'
                              '[ "$2" = --exe ] && [ -n "$VG_INSTALL" ] && mkdir -p "$1/$(dirname "$VG_INSTALL")" '
                              '&& touch "$1/$VG_INSTALL"\nexit 0\n')
        self.vgrun.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=f"{bin_}:{os.environ['PATH']}", VG_RUN=str(self.vgrun),
                        VG_ANS=str(self.answers), VG_CALLS=str(self.calls))

    def run_open(self, path, answers, install=None):
        self.answers.write_text("".join(a + "\n" for a in answers))
        env = dict(self.env, **({"VG_INSTALL": install} if install else {}))
        r = subprocess.run([sys.executable, str(VGOPEN), str(path)], env=env, capture_output=True, text=True, timeout=30)
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return r.returncode, calls

    def manifest(self, name):
        return json.loads((self.gd / name / ".vastgame.json").read_text())

    def test_installer_to_own_game(self):
        setup = self.f("Downloads/HoYoPlay_setup.exe")
        launcher = ".prefix/pfx/drive_c/Program Files/HoYoPlay/launcher.exe"
        code, calls = self.run_open(setup, ["0|0", "0|Genshin", "1|"], install=launcher)   # игра; название; «Готово»
        self.assertEqual(code, 0)
        self.assertIn(f"vg-run {self.gd}/Genshin --exe {setup}", calls)
        self.assertEqual(self.manifest("Genshin")["exe"], launcher)
        self.assertTrue(any(c.startswith("mark ") for c in calls))         # во время установки — метка «добавляется»
        self.assertEqual(list((self.home / ".local/share/vastgame/adding").iterdir()), [])   # после — убрана
        self.assertTrue((self.home / ".local/share/vastgame/requests/shortcuts").exists())   # ярлык Steam — wolf

    def test_portable_moved_with_its_folder(self):
        exe = self.f("Downloads/Hollow Knight/hollow_knight.exe")
        self.f("Downloads/Hollow Knight/data/a.dat")
        code, calls = self.run_open(exe, ["0|0", "0|1", "0|", "1|"])               # игра; «portable»; «да»; «Готово»
        self.assertEqual(code, 0)
        self.assertEqual(self.manifest("Hollow Knight")["exe"], "hollow_knight.exe")
        self.assertTrue((self.gd / "Hollow Knight/data/a.dat").exists())
        self.assertFalse((self.home / "Downloads/Hollow Knight").exists())

    def test_cancel_changes_nothing(self):
        exe = self.f("Downloads/Hollow Knight/hollow_knight.exe")
        code, _ = self.run_open(exe, ["0|0", "0|1", "1|"])                          # игра; «portable»; «Отмена»
        self.assertTrue(exe.exists())
        self.assertEqual(list(self.gd.iterdir()), [])

    def test_play_now_runs_the_game(self):
        exe = self.f("Downloads/Celeste.exe")
        code, calls = self.run_open(exe, ["0|0", "0|1", "0|", "0|"])               # игра; portable; да; «Играть сейчас»
        import time
        for _ in range(50):
            calls = self.calls.read_text().splitlines()
            if any(c.startswith("vg-run") for c in calls):
                break
            time.sleep(0.1)
        self.assertIn(f"vg-run {self.gd}/Celeste", calls)


class TestProgramsForGames(TestOpenFlows):
    """[v5.7] Шаг 4а: двойной щелчок по программе для игры — в Programs, игра выбирается сама или из списка."""

    def setUp(self):
        super().setUp()
        self.proc = self.home / "proc"
        self.proc.mkdir()
        self.env["VG_PROC"] = str(self.proc)
        self.env["VG_POLL"] = "0.05"
        self.pd = self.home / "Downloads/Programs"
        sa = self.home / ".steam/debian-installation/steamapps"
        for appid, name, d in (("2424420", "Avatar Legends", "Avatar"), ("489830", "Skyrim Special Edition", "Skyrim SE"),
                               ("1493710", "Proton Experimental", "Proton - Experimental"), ("70", "Half-Life", "HL")):
            (sa / f"appmanifest_{appid}.acf").parent.mkdir(parents=True, exist_ok=True)
            (sa / f"appmanifest_{appid}.acf").write_text(f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n'
                                                         f'\t"installdir"\t\t"{d}"\n}}\n')
            if appid != "70":                                               # Half-Life — паспорт есть, папки нет
                (sa / "common" / d).mkdir(parents=True)
        self.sa = sa

    def run_game(self, appid):
        (self.proc / "4242").mkdir()
        (self.proc / "4242/cmdline").write_bytes(f"reaper\0SteamLaunch\0AppId={appid}\0--\0x".encode())

    def texts(self, calls):
        return "\n".join(calls)                                       # текст окна с переносами — несколько строк журнала

    def test_decisions(self):
        vo = self.vo
        for f in ("Vortex-1.12.3-setup.exe", "WeMod-Setup-x64.exe", "skse64_loader.exe", "Fluffy Mod Manager 3.084.zip",
                  "ReShade_Setup_6.3.exe", "Cheat Engine 7.5.exe", "MO2-2.5.2.7z"):
            self.assertTrue(vo.is_tool(str(self.f("Downloads/" + f))), f)
        for f in ("HoYoPlay_setup.exe", "Celeste.exe", "Hollow Knight.zip"):
            self.assertFalse(vo.is_tool(str(self.f("Downloads/" + f))), f)
        games = vo.steam_games()
        self.assertEqual([g["name"] for g in games], ["Avatar Legends", "Skyrim Special Edition"])   # без Proton и без HL
        self.assertEqual(vo.game_choice(games, set()), (None, True))
        self.assertEqual(vo.game_choice(games, {"489830"})[0]["name"], "Skyrim Special Edition")
        self.assertEqual(vo.game_choice(games[:1], set())[0]["appid"], "2424420")
        self.assertEqual(vo.game_choice([], set()), (None, False))
        os.environ["VG_PROC"] = str(self.proc)
        self.addCleanup(os.environ.pop, "VG_PROC", None)
        self.run_game("489830")
        vo2 = load_vgopen(self.home)
        self.assertEqual(vo2.running_appids(), {"489830"})
        inside = self.f(".steam/debian-installation/steamapps/common/Skyrim SE/skse64_loader.exe")
        self.assertEqual(vo2.steam_game_of(str(inside))["appid"], "489830")

    def test_known_tool_while_game_runs_no_questions(self):
        self.run_game("489830")
        exe = self.f("Downloads/WeMod.exe")
        code, calls = self.run_open(exe, ["0|"])                          # только «готово»
        self.assertEqual(code, 0)
        self.assertTrue((self.pd / "WeMod/WeMod.exe").exists())
        self.assertFalse(exe.exists())
        self.assertNotIn("--list", self.texts(calls))                      # ни «что это», ни «для какой игры»
        self.assertEqual(len([c for c in calls if c.startswith("zenity")]), 1)
        self.assertIn("· Skyrim Special Edition", self.texts(calls))

    def test_unknown_archive_for_a_game_picked_from_list(self):
        import zipfile
        arc = self.home / "Downloads/SomeTool_v1.2.zip"
        with zipfile.ZipFile(arc, "w") as zf:
            zf.writestr("SomeTool/SomeTool.exe", b"MZ")
            zf.writestr("SomeTool/data/a.txt", b"x")
        code, calls = self.run_open(arc, ["0|1", "0|0", "0|", "0|"])      # программа; Avatar; распаковать; готово
        self.assertEqual(code, 0)
        self.assertTrue((self.pd / "SomeTool/SomeTool.exe").exists())     # общая папка сверху поднята
        self.assertTrue((self.pd / "SomeTool/data/a.txt").exists())
        self.assertIn("· Avatar Legends", self.texts(calls))
        self.assertEqual(list((self.home / ".local/share/vastgame/unpack").iterdir()), [])   # временное убрано
        self.assertFalse(any(p.name.endswith(".vgtmp") for p in self.pd.iterdir()))

    def machine(self, res="prog: installing", exes=("Program Files/Black Tree Gaming Ltd/Vortex/Vortex.exe",)):
        """Подставная машина: берёт просьбу из requests/install, отвечает и через миг сообщает итог установки."""
        import threading
        req, seen = self.home / ".local/share/vastgame/requests/install", {}

        def run():
            for _ in range(200):
                for f in req.glob("*.req") if req.is_dir() else []:
                    a, path = f.stem, f.read_text().strip()
                    seen.update(appid=a, path=path, exists=(self.home / ".local/share/vastgame/installers" / path).is_file())
                    f.unlink()
                    (req / f"{a}.res").write_text(res + "\n")
                    if res == "prog: installing":
                        time.sleep(0.2)
                        (req / f"{a}.done").write_text(json.dumps({"exes": list(exes)}))
                    return
                time.sleep(0.02)
        threading.Thread(target=run, daemon=True).start()
        return seen

    def test_installer_into_game(self):
        setup = self.f("Downloads/Vortex-1.12.3-setup.exe")
        seen = self.machine()
        code, calls = self.run_open(setup, ["0|0", "0|"])                  # Avatar; «понятно»
        self.assertEqual(code, 0)
        self.assertEqual(seen, {"appid": "2424420", "path": "Vortex/Vortex-1.12.3-setup.exe", "exists": True})
        self.assertIn("Vortex.exe", self.texts(calls))
        self.assertTrue(setup.exists())                                    # твой файл на месте
        self.assertEqual(list((self.home / ".local/share/vastgame/installers").iterdir()), [])   # копия убрана
        self.assertFalse(self.pd.exists() and any(self.pd.iterdir()))

    def test_installer_refused_keeps_file(self):
        setup = self.f("Downloads/Vortex-1.12.3-setup.exe")
        self.machine(res="prog: notinstalled")
        code, calls = self.run_open(setup, ["0|0", "0|"])
        self.assertIn("hasn't downloaded", self.texts(calls))
        self.assertTrue(setup.exists())
        self.assertEqual(list((self.home / ".local/share/vastgame/installers").iterdir()), [])

    def test_installer_while_game_runs_cancel(self):
        self.run_game("489830")
        setup = self.f("Downloads/Vortex-1.12.3-setup.exe")
        code, calls = self.run_open(setup, ["1|"])                         # «закрой игру» → «Отмена»
        self.assertIn("is running", self.texts(calls))
        self.assertTrue(setup.exists())
        self.assertFalse((self.home / ".local/share/vastgame/requests/install").exists())

    def test_inside_steam_game_and_already_in_programs(self):
        exe = self.f(".steam/debian-installation/steamapps/common/Skyrim SE/skse64_loader.exe")
        code, calls = self.run_open(exe, ["0|"])
        self.assertTrue(exe.exists())
        self.assertIn("Skyrim Special Edition", self.texts(calls))
        self.assertEqual(list(self.gd.iterdir()), [])
        self.calls.unlink()
        exe2 = self.f("Downloads/Programs/WeMod/WeMod.exe")
        code, calls = self.run_open(exe2, ["0|"])
        self.assertTrue(exe2.exists())
        self.assertEqual(len([c for c in calls if c.startswith("zenity")]), 1)

    def test_cancel_purpose_changes_nothing(self):
        exe = self.f("Downloads/Unknown.exe")
        code, calls = self.run_open(exe, ["1|"])
        self.assertEqual(code, 0)
        self.assertTrue(exe.exists())
        self.assertFalse(self.pd.exists() and any(self.pd.iterdir()))
        self.assertEqual(list(self.gd.iterdir()), [])


for _n in [n for n in vars(TestOpenFlows) if n.startswith("test_")]:   # сценарии своих игр — только в TestOpenFlows
    setattr(TestProgramsForGames, _n, None)


class TestOwnSetup(unittest.TestCase):
    def test_mime_binding_keeps_other_defaults(self):
        from test_game_select import Machine, fn
        m = Machine()
        cfg = m.d / "home/.config"
        cfg.mkdir(parents=True)
        (cfg / "mimeapps.list").write_text("[Default Applications]\ntext/plain=mousepad.desktop\n"
                                           "application/zip=xarchiver.desktop\n\n[Added Associations]\nx/y=z.desktop\n")
        own_mime = re.search(r'^OWN_MIME="[^"]*"', WOLF, re.M | re.S).group(0)
        for _ in range(2):
            m.run(own_mime + "\n" + fn("own_setup") + "\nSL=russian; own_setup")
        text = (cfg / "mimeapps.list").read_text()
        self.assertIn("text/plain=mousepad.desktop", text)
        self.assertIn("application/zip=vastgame-open.desktop", text)
        self.assertNotIn("xarchiver", text)
        self.assertEqual(text.count("application/x-ms-dos-executable=vastgame-open.desktop"), 1)   # не задваивается
        self.assertIn("[Added Associations]\nx/y=z.desktop", text)
        self.assertEqual((m.d / "home/.local/share/vastgame/lang").read_text().strip(), "ru")
        desk = (m.d / "home/.local/share/applications/vastgame-open.desktop").read_text()
        self.assertIn("Exec=/usr/local/bin/vg-open %f", desk)
        self.assertIn("application/x-msi;", desk)


import re  # noqa: E402


class TestDesktopIcons(unittest.TestCase):
    def test_icons_follow_own_games(self):
        from test_game_select import Machine, fn
        m = Machine()
        desk = m.d / "home/Desktop"
        desk.mkdir()
        g = m.d / "home/Games/Hollow Knight"
        g.mkdir(parents=True)
        (g / ".vastgame.json").write_text('{"exe": "hk.exe"}')
        (m.d / "home/Games/Plain").mkdir()                                    # без паспорта — значка нет
        (desk / "chrome.desktop").write_text("[Desktop Entry]\nName=Chrome\n")
        script = fn("own_icons") + "\nown_icons; own_icons"
        m.run(script)
        icon = desk / "Hollow Knight.desktop"
        text = icon.read_text()
        self.assertIn(f'Exec=/usr/local/bin/vg-run "{g}"', text)
        self.assertIn("X-VastGame=own", text)
        self.assertTrue(os.access(icon, os.X_OK))
        self.assertEqual(sorted(x.name for x in desk.iterdir()), ["Hollow Knight.desktop", "chrome.desktop"])
        icon.unlink()                                                         # человек убрал значок — не возвращаем
        m.run(script)
        self.assertFalse(icon.exists())
        (m.d / "S/own.icons").unlink()                                        # новая машина — значок снова есть
        m.run(script)
        self.assertTrue(icon.exists())
        import shutil
        shutil.rmtree(g)                                                      # игру удалили — значок убран
        m.run(script)
        self.assertFalse(icon.exists())
        self.assertTrue((desk / "chrome.desktop").exists())                   # чужие значки не трогаем

    def test_noexec_wiring(self):
        f = re.search(r"^own_noexec\(\) \{\n.*?\n\}$", WOLF, re.S | re.M).group(0)
        self.assertIn("-name .prefix -o -name steamapps", f)
        self.assertIn("-iname '*.exe' -o -iname '*.msi'", f)
        self.assertIn("own_icons; own_noexec", WOLF)


if __name__ == "__main__":
    unittest.main()

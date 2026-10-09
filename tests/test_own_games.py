# wolf-setup.sh v5.7: своя игра (папка Games с паспортом .vastgame.json и своим «диском C:» .prefix) — ярлык Steam на
# запускатель vg-run (без Proton у ярлыка), запуск vg-run через вход Steam Linux Runtime и Proton, как делает Steam
# (разведка 2026-10-09). Настоящий код из heredoc; Steam, Proton и Runtime — поддельные папки и скрипты.

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_game_select import TMP, WOLF, heredoc  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")


def extract(path, tag):
    match = re.search(
        rf"^w {re.escape(path)} <<'{tag}'\n(.*?)\n{tag}$",
        SOURCE,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError(f"cannot extract {path}")
    return match.group(1)


def load_module(name, path, tag, directory):
    target = Path(directory) / f"{name}.py"
    target.write_text(extract(path, tag), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LaunchersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self.temp.name))
        self.root = self.base / "Steam"
        self.common = self.root / "steamapps" / "common"
        self.runtime = self.common / "SteamLinuxRuntime_4"
        self.proton = self.common / "Proton - Experimental"
        self.runtime.mkdir(parents=True)
        self.proton.mkdir(parents=True)
        (self.root / "config").mkdir()
        self.entry = self.runtime / "_v2-entry-point"
        self.entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.entry.chmod(0o755)
        self.proton_bin = self.proton / "proton"
        self.proton_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.proton_bin.chmod(0o755)
        (self.proton / "toolmanifest.vdf").write_text(
            '"tool"\n{\n\t"require_tool_appid"\t\t"1623730"\n}\n', encoding="utf-8"
        )
        (self.root / "steamapps" / "appmanifest_1623730.acf").write_text(
            '"AppState"\n{\n\t"installdir"\t\t"SteamLinuxRuntime_4"\n}\n', encoding="utf-8"
        )
        (self.root / "config" / "config.vdf").write_text(
            '"CompatToolMapping"\n{\n\t"0"\n\t{\n\t\t"name"\t\t"proton_experimental"\n\t}\n}\n',
            encoding="utf-8",
        )
        self.game = self.base / "Game"
        self.game.mkdir()
        (self.game / "Game.exe").write_bytes(b"game")
        (self.game / ".vastgame.json").write_text('{"exe":"Game.exe","args":"--from-manifest"}', encoding="utf-8")
        self.iso = self.base / "isolation.env"
        self.iso.write_text(
            "BWRAP=/usr/local/lib/proot-next/bwrap\n"
            "PRESSURE_VESSEL_BWRAP=/usr/local/lib/proot-next/bwrap\n",
            encoding="utf-8",
        )
        self.vgrun = load_module("vg_run_test", "/usr/local/bin/vg-run", "VGRUN", self.base)
        self.vgrun.steam_root = lambda: str(self.root)
        self.vgrun.ISO_ENV = str(self.iso)
        self.steam = load_module("wolf_steam_test", "/usr/local/bin/wolf-steam.py", "STEAMPY", self.base)
        self.steam.ISO_ENV = str(self.iso)

    def tearDown(self):
        self.temp.cleanup()

    def test_vg_run_uses_runtime_same_prefix_and_isolation(self):
        command, why = self.vgrun.command(str(self.game))
        self.assertIsNone(why)
        argv, env, cwd, exe = command
        self.assertEqual(argv, [
            str(self.entry), "--verb=waitforexitandrun", "--", str(self.proton_bin),
            "waitforexitandrun", str(self.game / "Game.exe"), "--from-manifest",
        ])
        self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(self.game / ".prefix"))
        self.assertEqual(env["STEAM_COMPAT_CLIENT_INSTALL_PATH"], str(self.root))
        self.assertEqual(env["BWRAP"], "/usr/local/lib/proot-next/bwrap")
        self.assertEqual(env["PRESSURE_VESSEL_BWRAP"], "/usr/local/lib/proot-next/bwrap")
        self.assertEqual(cwd, str(self.game))
        self.assertEqual(exe, str(self.game / "Game.exe"))

    def test_vg_run_installer_keeps_runtime_and_prefix(self):
        installer = self.game / "Mod Installer.exe"
        installer.write_bytes(b"installer")
        command, why = self.vgrun.command(str(self.game), str(installer), ["--silent"])
        self.assertIsNone(why)
        argv, env, _, _ = command
        self.assertEqual(argv[:4], [str(self.entry), "--verb=waitforexitandrun", "--", str(self.proton_bin)])
        self.assertEqual(argv[-3:], ["waitforexitandrun", str(installer), "--silent"])
        self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(self.game / ".prefix"))

    def test_wolf_steam_offline_mod_manager_uses_game_runtime_and_prefix(self):
        head, game, env = self.steam._offline_parts(self.root, 3527290)
        self.assertIsNone(game)
        self.assertEqual(head, [str(self.entry), "--verb=run", "--", str(self.proton_bin)])
        prefix = self.root / "steamapps" / "compatdata" / "3527290"
        self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(prefix))
        self.assertEqual(env["SteamAppId"], "3527290")
        self.assertEqual(env["BWRAP"], "/usr/local/lib/proot-next/bwrap")
        self.assertEqual(env["PRESSURE_VESSEL_BWRAP"], "/usr/local/lib/proot-next/bwrap")

    def test_vg_launch_replaces_game_and_preserves_isolation_and_preload(self):
        script = self.base / "vg-launch"
        script.write_text(extract("/usr/local/bin/vg-launch", "VGLAUNCH"), encoding="utf-8")
        script.chmod(0o755)
        home = self.base / "home"
        launch = home / ".local" / "share" / "vastgame" / "launch"
        launch.mkdir(parents=True)
        target = self.game / "Mod Loader.exe"
        target.write_bytes(b"loader")
        capture = self.base / "capture.json"
        capture.write_text("{}", encoding="utf-8")
        recorder = self.base / "record.py"
        recorder.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': {k: os.environ.get(k) "
            "for k in ('BWRAP', 'PRESSURE_VESSEL_BWRAP', 'LD_PRELOAD', 'SDL_JOYSTICK_DISABLE_UDEV', "
            "'PROTON_USE_XALIA')}}, open(os.environ['CAPTURE'], 'w'))\n",
            encoding="utf-8",
        )
        recorder.chmod(0o755)
        for suffix in ("", ".once"):
            with self.subTest(suffix=suffix):
                config = launch / f"3527290{suffix}"
                config.write_text(f"{target}\n--load base\n", encoding="utf-8")
                env = dict(os.environ,
                           HOME=str(home), SteamAppId="3527290", CAPTURE=str(capture),
                           BWRAP="/usr/local/lib/proot-next/bwrap",
                           PRESSURE_VESSEL_BWRAP="/usr/local/lib/proot-next/bwrap",
                           LD_PRELOAD="libvgpad.so", SDL_JOYSTICK_DISABLE_UDEV="1")
                result = subprocess.run([
                    str(script), str(recorder), "--verb=waitforexitandrun", "--",
                    str(self.proton_bin), "waitforexitandrun", str(self.game / "Game.exe"), "--old",
                ], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(capture.read_text(encoding="utf-8"))
                self.assertEqual(data["argv"], [
                    "--verb=waitforexitandrun", "--", str(self.proton_bin),
                    "waitforexitandrun", str(target), "--load", "base",
                ])
                self.assertEqual(data["cwd"], os.path.realpath(target.parent))
                self.assertEqual(data["env"], {
                    "BWRAP": "/usr/local/lib/proot-next/bwrap",
                    "PRESSURE_VESSEL_BWRAP": "/usr/local/lib/proot-next/bwrap",
                    "LD_PRELOAD": "libvgpad.so",
                    "SDL_JOYSTICK_DISABLE_UDEV": "1",
                    "PROTON_USE_XALIA": "0",
                })
                if suffix:
                    self.assertFalse(config.exists())
                    self.assertTrue((launch / "3527290.ran").exists())
                    (launch / "3527290.ran").unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)

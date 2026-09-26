# Игры из папки Games — в библиотеку Steam (wolf-setup.sh v4.1, wolf-shortcuts.py из heredoc): настоящий код
# на поддельных папках игр и Steam. Проверяем, что Steam получит правильный shortcuts.vdf и Proton.

import binascii
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = re.search(r"^w /usr/local/bin/wolf-shortcuts.py <<'SHORTCUTS'\n(.*?)^SHORTCUTS$",
                 (ROOT / "wolf-setup.sh").read_text(), re.S | re.M).group(1)
CONFIG = '''"InstallConfigStore"
{
\t"Software"
\t{
\t\t"Valve"
\t\t{
\t\t\t"Steam"
\t\t\t{
\t\t\t\t"CompatToolMapping"
\t\t\t\t{
\t\t\t\t\t"0"
\t\t\t\t\t{
\t\t\t\t\t\t"name"\t\t"proton_10"
\t\t\t\t\t\t"config"\t\t""
\t\t\t\t\t\t"priority"\t\t"75"
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\t"ConnectCache"\t\t"xyz"
\t\t\t}
\t\t}
\t}
}
'''


class Base(unittest.TestCase):
    """Поддельные папки игр и Steam, запуск настоящего помощника."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.script = self.tmp / "wolf-shortcuts.py"
        self.script.write_text(CODE)
        spec = importlib.util.spec_from_file_location("ws", self.script)
        self.ws = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ws)
        self.games = self.tmp / "Games"
        self.steam = self.tmp / "Steam"
        (self.steam / "userdata/123/config").mkdir(parents=True)
        (self.steam / "config").mkdir()
        (self.steam / "config/config.vdf").write_text(CONFIG)
        self.game("The_Blood_of_Dawnwalker", {"Dawnwalker.exe": 300, "Engine/Binaries/Win64/Shipping.exe": 9000,
                                              "unins000.exe": 5000, "UnityCrashHandler64.exe": 7000})
        self.game("Notes", {"readme.txt": 10})                     # без .exe — не игра

    def game(self, name, files):
        for rel, size in files.items():
            f = self.games / name / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x" * size)

    def run_it(self, *extra):
        return subprocess.run([sys.executable, str(self.script), str(self.games), str(self.steam), *extra],
                              capture_output=True, text=True).returncode

    def shortcuts(self):
        data = (self.steam / "userdata/123/config/shortcuts.vdf").read_bytes()
        root, end = self.ws.bkv_read(data, 0)
        self.assertEqual(end, len(data))
        return list(root["shortcuts"].values())


class TestShortcuts(Base):
    def test_added_with_steam_appid_and_proton(self):
        self.assertEqual(self.run_it(), 3)
        [e] = self.shortcuts()
        exe = str(self.games / "The_Blood_of_Dawnwalker/Dawnwalker.exe")   # верхний уровень, не деинсталлятор
        self.assertEqual(e["Exe"], f'"{exe}"')
        self.assertEqual(e["AppName"], "The Blood of Dawnwalker")
        want = (binascii.crc32(f'"{exe}"The Blood of Dawnwalker'.encode()) & 0xFFFFFFFF) | 0x80000000
        self.assertEqual(e["appid"][1] & 0xFFFFFFFF, want)                  # сохранения: pfx--<want>
        cfg = (self.steam / "config/config.vdf").read_text()
        self.assertRegex(cfg, rf'"{want}"\s*\{{\s*"name"\s*"proton_10"')    # Proton «для всех игр»
        self.assertIn('"ConnectCache"\t\t"xyz"', cfg)                      # остальное на месте (вход в Steam)

    def test_second_run_changes_nothing(self):
        self.run_it()
        before = (self.steam / "userdata/123/config/shortcuts.vdf").read_bytes()
        self.assertEqual(self.run_it(), 0)
        self.assertEqual((self.steam / "userdata/123/config/shortcuts.vdf").read_bytes(), before)

    def test_check_does_not_write(self):
        self.assertEqual(self.run_it("--check"), 3)
        self.assertFalse((self.steam / "userdata/123/config/shortcuts.vdf").exists())

    def test_folder_removed_game_removed_manual_kept(self):
        manual = {"appid": ("int", -5), "AppName": "Моя игра", "Exe": '"/opt/my/game.exe"', "tags": {}}
        (self.steam / "userdata/123/config/shortcuts.vdf").write_bytes(self.ws.bkv_write({"shortcuts": {"0": manual}}))
        self.run_it()
        self.assertEqual(len(self.shortcuts()), 2)
        import shutil
        shutil.rmtree(self.games / "The_Blood_of_Dawnwalker")
        self.assertEqual(self.run_it(), 3)
        self.assertEqual([e["AppName"] for e in self.shortcuts()], ["Моя игра"])

    def test_game_added_by_hand_not_duplicated(self):
        exe = str(self.games / "The_Blood_of_Dawnwalker/Engine/Binaries/Win64/Shipping.exe")
        manual = {"appid": ("int", -7), "AppName": "Dawnwalker", "Exe": f'"{exe}"', "tags": {}}
        (self.steam / "userdata/123/config/shortcuts.vdf").write_bytes(self.ws.bkv_write({"shortcuts": {"0": manual}}))
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(len(self.shortcuts()), 1)

    def test_broken_file_left_alone(self):
        f = self.steam / "userdata/123/config/shortcuts.vdf"
        f.write_bytes(b"\x05garbage")
        self.assertIn(self.run_it(), (0, 3))
        self.assertEqual(f.read_bytes(), b"\x05garbage")


LOCALCONFIG = '''"UserLocalConfigStore"
{
	"apps"
	{
		"3751260"
		{
			"UseSteamControllerConfig"		"2"
		}
	}
	"friends"
	{
		"PersonaName"		"Имя \\"в кавычках\\""
	}
}
'''


class TestSteamInput(Base):
    """v4.3: Steam Input выключен по умолчанию — UseSteamControllerConfig "0" там, где человек сам не выбирал."""

    def setUp(self):
        super().setUp()
        self.lc = self.steam / "userdata/123/config/localconfig.vdf"
        self.lc.write_text(LOCALCONFIG)
        (self.steam / "steamapps").mkdir()
        for aid in (10, 3751260):
            (self.steam / f"steamapps/appmanifest_{aid}.acf").write_text('"AppState"\n{\n}\n')

    def setting(self, aid):
        items = self.ws.tkv_parse(self.lc.read_text())
        block = self.ws.tkv_get(self.ws.tkv_get(self.ws.tkv_get(items, "UserLocalConfigStore"), "apps"), str(aid))
        return dict(block or []).get("UseSteamControllerConfig")

    def test_off_by_default_own_choice_kept(self):
        self.assertEqual(self.run_it("--steam-input"), 3)
        self.assertEqual(self.setting(10), "0")                 # игра без выбора — выключен
        self.assertEqual(self.setting(3751260), "2")            # человек включил сам — не трогаем
        self.assertIn('"PersonaName"\t\t"Имя \\"в кавычках\\""', self.lc.read_text())   # остальное на месте
        self.assertEqual(self.run_it("--steam-input"), 0)       # второй раз — ничего

    def test_games_folder_shortcut_gets_it_too(self):
        self.run_it()
        [e] = self.shortcuts()
        self.assertEqual(self.setting(e["appid"][1] & 0xFFFFFFFF), "0")

    def test_no_localconfig_nothing_created(self):
        self.lc.unlink()
        self.assertEqual(self.run_it("--steam-input"), 0)
        self.assertFalse(self.lc.exists())


if __name__ == "__main__":
    unittest.main()

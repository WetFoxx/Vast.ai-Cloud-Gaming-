# wolf-setup.sh v5.6, правки по живой проверке FF14 (2026-10-08): сведения Steam об игре из appcache/appinfo.vdf
# (настоящая папка установки и системы — форматы 28 и 29), паспорт игры с этой папкой, «показывать скрытые» в реестре
# «диска C:» игр, NVAPI выключен на машинах с несколькими видеокартами или без RTX. Настоящий код из heredoc.

import importlib.util
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_game_select import TMP, WOLF, fn, heredoc  # noqa: E402


def load(name, tag, mod):
    p = TMP / f"{mod}.py"
    p.write_text(heredoc(name, tag))
    spec = importlib.util.spec_from_file_location(mod, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


short = load("wolf-shortcuts.py", "SHORTCUTS", "short_appinfo")
ws = load("wolf-steam.py", "STEAMPY", "steam_appinfo")


def kv(d, table):
    """Двоичный KeyValues; table — None (формат 28: ключи строками) или список (29: номера в таблице строк)."""
    out = b""
    for k, v in d.items():
        if isinstance(v, dict):
            t, body = 0x00, kv(v, table) + b"\x08"
        elif isinstance(v, int):
            t, body = 0x02, struct.pack("<i", v)
        else:
            t, body = 0x01, v.encode() + b"\0"
        if table is None:
            key = k.encode() + b"\0"
        else:
            if k not in table:
                table.append(k)
            key = struct.pack("<I", table.index(k))
        out += bytes([t]) + key + body
    return out


def appinfo_file(path, apps, version=29):
    table = [] if version == 29 else None
    body = b""
    for aid, info in apps.items():
        data = b"\0" * 60 + kv({"appinfo": info}, table) + b"\x08"
        body += struct.pack("<II", aid, len(data)) + data
    body += struct.pack("<I", 0)
    if version == 29:
        head = struct.pack("<II", 0x07564429, 1)
        off = len(head) + 8 + len(body)
        strings = struct.pack("<I", len(table)) + b"".join(s.encode() + b"\0" for s in table)
        path.write_bytes(head + struct.pack("<q", off) + body + strings)
    else:
        path.write_bytes(struct.pack("<II", 0x07564428, 1) + body)


TRIAL = {"appid": 312060, "common": {"name": "FINAL FANTASY XIV Online Free Trial", "oslist": "windows"},
         "config": {"installdir": "FINAL FANTASY XIV Online"}}
OTHER = {"appid": 570, "common": {"name": "Dota 2", "oslist": "windows,macos,linux"}, "config": {"installdir": "dota 2 beta"}}


class TestAppinfo(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))
        (self.root / "appcache").mkdir()

    def test_both_formats(self):
        for version in (28, 29):
            appinfo_file(self.root / "appcache/appinfo.vdf", {570: OTHER, 312060: TRIAL}, version)
            self.assertEqual(short.appinfo(str(self.root), "312060"),
                             {"installdir": "FINAL FANTASY XIV Online", "oslist": "windows"}, version)
            self.assertEqual(short.appinfo(str(self.root), "570")["oslist"], "windows,macos,linux")

    def test_unknown_game_or_no_cache(self):
        self.assertIsNone(short.appinfo(str(self.root), "312060"))            # кэша нет (новый человек)
        appinfo_file(self.root / "appcache/appinfo.vdf", {570: OTHER})
        self.assertIsNone(short.appinfo(str(self.root), "312060"))
        (self.root / "appcache/appinfo.vdf").write_bytes(b"\x27\x44\x56\x07" + b"\0" * 20)   # другой формат
        self.assertIsNone(short.appinfo(str(self.root), "570"))
        (self.root / "appcache/appinfo.vdf").write_bytes(struct.pack("<II", 0x07564428, 1) + b"\x01")  # обрезан
        self.assertIsNone(short.appinfo(str(self.root), "570"))

    def test_command_line(self):
        appinfo_file(self.root / "appcache/appinfo.vdf", {312060: TRIAL})
        r = subprocess.run([sys.executable, TMP / "short_appinfo.py", str(self.root / "Games"), str(self.root),
                            "--appinfo", "312060"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip("\n"), "FINAL FANTASY XIV Online\twindows")


class TestManifestFolder(unittest.TestCase):
    def test_folder_from_steam(self):
        root = Path(tempfile.mkdtemp(dir=TMP))
        ws.manifest(root, "312060", "76561198000000000", "FINAL FANTASY XIV Online Free Trial", "FINAL FANTASY XIV Online")
        acf = (root / "steamapps/appmanifest_312060.acf").read_text()
        self.assertIn('"installdir"\t\t"FINAL FANTASY XIV Online"', acf)

    def test_bad_folder_ignored(self):
        root = Path(tempfile.mkdtemp(dir=TMP))
        for bad in ("../x", "..", "a/b"):
            subprocess.run([sys.executable, TMP / "steam_appinfo.py", "manifest", str(root), "312060", "765", "Trial", bad],
                           capture_output=True)
            acf = (root / "steamapps/appmanifest_312060.acf").read_text()
            self.assertNotIn(bad, acf.split('"installdir"')[1].split("\n")[0])
            (root / "steamapps/appmanifest_312060.acf").unlink()


class TestShowDotFiles(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))

    def reg(self, appid, text):
        p = self.root / "steamapps/compatdata" / appid / "pfx/user.reg"
        p.parent.mkdir(parents=True)
        p.write_text(text)
        return p

    def test_into_wine_section_once(self):
        a = self.reg("312060", 'WINE REGISTRY Version 2\n\n[Software\\\\Wine] 1791000000\n#time=1dc\n"Version"="win10"\n')
        b = self.reg("570", 'WINE REGISTRY Version 2\n\n[Control Panel\\\\Desktop] 1791000000\n"x"="1"\n')
        c = self.reg("1", 'WINE REGISTRY Version 2\n[Software\\\\Wine] 1\n"ShowDotFiles"="N"\n')     # свой выбор
        for _ in range(2):
            ws.pfx_tweaks(self.root)
        self.assertEqual(a.read_text().count("ShowDotFiles"), 1)
        self.assertIn('#time=1dc\n"ShowDotFiles"="Y"\n"Version"', a.read_text())
        self.assertRegex(b.read_text(), r'\n\[Software\\\\Wine\] \d+\n"ShowDotFiles"="Y"\n$')
        self.assertEqual(b.read_text().count("ShowDotFiles"), 1)
        self.assertIn('"ShowDotFiles"="N"', c.read_text())


class TestNvapi(unittest.TestCase):
    def run_check(self, smi):
        d = Path(tempfile.mkdtemp(dir=TMP))
        (d / "S").mkdir()
        (d / "S/nvapi.off").touch()
        script = f'''
S="{d}/S"
log() {{ echo "LOG $*"; }}
nvidia-smi() {{ printf '%b' {smi!r}; }}
{fn("nvapi_check")}
nvapi_check
'''
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
        return (d / "S/nvapi.off").exists(), out

    def test_cases(self):
        one_rtx = "GPU 0: NVIDIA GeForce RTX 3060 (UUID: GPU-1)\n"
        two = "GPU 0: NVIDIA GeForce GTX 1650 (UUID: GPU-1)\nGPU 1: NVIDIA GeForce GTX 1650 (UUID: GPU-2)\n"
        two_rtx = "GPU 0: NVIDIA GeForce RTX 3060 (UUID: GPU-1)\nGPU 1: NVIDIA GeForce RTX 3060 (UUID: GPU-2)\n"
        gtx = "GPU 0: NVIDIA GeForce GTX 1070 (UUID: GPU-1)\n"
        self.assertEqual(self.run_check(one_rtx)[0], False)
        off, out = self.run_check(two)
        self.assertTrue(off)
        self.assertIn("2 × NVIDIA GeForce GTX 1650", out)
        self.assertTrue(self.run_check(two_rtx)[0])
        self.assertTrue(self.run_check(gtx)[0])

    def test_steam_started_without_nvapi(self):
        go = fn("steam_go")
        self.assertIn("PROTON_DISABLE_NVAPI=1", go)
        self.assertIn('[ -e "$S/nvapi.off" ]', go)
        boot = fn("launch_boot")
        for s in ("nvapi_check", "pfx-tweaks", 'ln -s "$DH/$SR/steamapps/common" "$DH/SteamGames"'):
            self.assertIn(s, boot)


class TestPlayPrepare(unittest.TestCase):
    def test_wiring(self):
        f = fn("play_prepare")
        self.assertIn('--appinfo "$a"', f)
        self.assertIn('"$(rd "$S/play.name")" "$dir"', f)                   # папка из сведений — в паспорт
        self.assertIn("[[ $os != *linux* ]]", f)
        self.assertIn('--proton-for "$a"', f)
        self.assertIn("launch_boot", WOLF)


if __name__ == "__main__":
    unittest.main()

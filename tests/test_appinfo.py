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
games = load("wolf-games.py", "GAMES", "games_appinfo")
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


# U+0100 в UTF-16LE начинается с байта 00. Поиск «до первого нуля» обрезал бы строку и съехал бы со следующих полей.
WIDE = "A\u0100B"
BLOB = b"\x08\x00\x00\x00\x00\xff"


def _key(name, table):
    if table is None:
        return name.encode() + b"\0"
    if name not in table:
        table.append(name)
    return struct.pack("<I", table.index(name))


def _leaf(typ, name, payload, table):
    return bytes([typ]) + _key(name, table) + payload


def _obj(name, inner, table):
    return bytes([0x00]) + _key(name, table) + inner + b"\x08"


def _wstring(text):
    return text.encode("utf-16-le") + b"\x00\x00"


def _app_tree(table, installdir, name, oslist="windows", prefix=b""):
    common = _leaf(0x05, "name", _wstring(name), table) + _leaf(0x01, "oslist", oslist.encode() + b"\0", table)
    config = _leaf(0x01, "installdir", installdir.encode() + b"\0", table)
    return _obj("appinfo", prefix + _obj("common", common, table) + _obj("config", config, table), table)


def _prefix(table):
    """float32, Binary и WString до имени и папки: их длина не должна сдвинуть следующие поля."""
    return (_leaf(0x03, "score", struct.pack("<f", 1.5), table)
            + _leaf(0x09, "hash", struct.pack("<I", len(BLOB)) + BLOB, table)
            + _leaf(0x05, "label", _wstring(WIDE), table))


def _pack(records, version, table):
    body = b""
    for aid, kv in records:
        chunk = b"\0" * 60 + kv
        body += struct.pack("<II", aid, len(chunk)) + chunk
    body += struct.pack("<I", 0)
    if version == 28:
        return struct.pack("<II", 0x07564428, 1) + body
    head = struct.pack("<II", 0x07564429, 1)
    off = len(head) + 8 + len(body)
    strings = struct.pack("<I", len(table)) + b"".join(s.encode() + b"\0" for s in table)
    return head + struct.pack("<q", off) + body + strings


class TestAppinfoTypes(unittest.TestCase):
    """Обе копии разбора: wolf-shortcuts.py (appinfo) и wolf-games.py (_read_appinfo / _appinfo_kv)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))
        (self.root / "appcache").mkdir()
        (self.root / "steamapps").mkdir()

    def _write(self, blob):
        (self.root / "appcache/appinfo.vdf").write_bytes(blob)

    def test_leaves_match_steam_layout(self):
        # Прямой разбор дерева, формат 28: ключи строками. Float — не биты int32.
        tree = (_prefix(None) + _leaf(0x01, "after", b"ok\0", None) + b"\x08")
        for label, mod in (("shortcuts", short), ("games", games)):
            got, _, ok = mod._appinfo_kv(tree, 0, None, len(tree))
            self.assertTrue(ok, label)
            self.assertEqual(got["score"], 1.5, label)
            self.assertNotEqual(got["score"], struct.unpack("<i", struct.pack("<f", 1.5))[0], label)
            self.assertEqual(got["hash"], BLOB, label)
            self.assertEqual(got["label"], WIDE, label)
            self.assertEqual(got["after"], "ok", label)

    def test_alternate_end_closes_only_the_block(self):
        # 0x0B — второй маркер конца блока у Steam, без своего тела. Следующее поле того же родителя читается.
        tree = (bytes([0x00]) + _key("box", None) + _leaf(0x01, "a", b"x\0", None) + b"\x0b"
                + _leaf(0x01, "installdir", b"SafeDir\0", None) + b"\x08")
        for label, mod in (("shortcuts", short), ("games", games)):
            got, _, ok = mod._appinfo_kv(tree, 0, None, len(tree))
            self.assertTrue(ok, label)
            self.assertEqual(got["box"]["a"], "x", label)
            self.assertEqual(got["installdir"], "SafeDir", label)

    def test_wstring_binary_and_float_keep_installdir(self):
        for version in (28, 29):
            table = [] if version == 29 else None
            kv570 = _app_tree(table, "SafeDir", WIDE, prefix=_prefix(table))
            blob = _pack([(570, kv570)], version, table if table is not None else [])
            self._write(blob)
            for label, read in (("shortcuts", lambda: short.appinfo(str(self.root), "570")),
                                ("games", lambda: games._read_appinfo(str(self.root / "steamapps")).get("570"))):
                got = read()
                self.assertEqual(got["installdir"], "SafeDir", (label, version))
            self.assertEqual(short.appinfo(str(self.root), "570")["oslist"], "windows")
            self.assertEqual(games._read_appinfo(str(self.root / "steamapps"))["570"]["name"], WIDE)
            # Значения float/binary/wstring из той же записи, оба парсера.
            data = (self.root / "appcache/appinfo.vdf").read_bytes()
            for label, mod in (("shortcuts", short), ("games", games)):
                apps = dict(mod._appinfo_apps(data))
                app = apps[570]
                self.assertEqual(app["score"], 1.5, (label, version))
                self.assertEqual(app["hash"], BLOB, (label, version))
                self.assertEqual(app["label"], WIDE, (label, version))

    def test_unknown_type_keeps_parsed_fields_and_the_next_app(self):
        for version in (28, 29):
            table = [] if version == 29 else None
            # 570: папка уже прочитана, дальше неизвестный тип 0x7F и мусор, похожий на конец чужой записи.
            good = _obj("config", _leaf(0x01, "installdir", b"SafeDir\0", table), table)
            junk = bytes([0x7F]) + _key("nope", table) + b"\x08\x00" + b"ONLY730"
            kv570 = _obj("appinfo", good + junk, table)
            kv730 = _app_tree(table, "ONLY730", "Counter-Strike 2")
            self._write(_pack([(570, kv570), (730, kv730)], version, table if table is not None else []))
            self.assertEqual(short.appinfo(str(self.root), "570")["installdir"], "SafeDir")
            self.assertEqual(short.appinfo(str(self.root), "730")["installdir"], "ONLY730")
            self.assertNotIn("ONLY730", short.appinfo(str(self.root), "570")["installdir"])
            found = games._read_appinfo(str(self.root / "steamapps"))
            self.assertEqual(found["570"]["installdir"], "SafeDir", version)
            self.assertEqual(found["730"]["installdir"], "ONLY730", version)
            self.assertEqual(found["730"]["name"], "Counter-Strike 2", version)

    def test_does_not_read_past_the_record_into_the_next_app(self):
        for version in (28, 29):
            table = [] if version == 29 else None
            # Строка без нуля упирается в границу size. Нуль есть только в следующей записи.
            spilled = (_obj("config", _leaf(0x01, "installdir", b"SafeDir\0", table), table)
                       + _leaf(0x01, "spill", b"NOTERM", table))
            kv570 = bytes([0x00]) + _key("appinfo", table) + spilled
            kv730 = _app_tree(table, "ONLY730", "Next")
            self._write(_pack([(570, kv570), (730, kv730)], version, table if table is not None else []))
            self.assertEqual(short.appinfo(str(self.root), "570")["installdir"], "SafeDir")
            self.assertEqual(short.appinfo(str(self.root), "730")["installdir"], "ONLY730")
            found = games._read_appinfo(str(self.root / "steamapps"))
            self.assertEqual(found["570"]["installdir"], "SafeDir", version)
            self.assertEqual(found["730"]["installdir"], "ONLY730", version)
            # Binary с длиной больше остатка записи не захватывает байты 730.
            table2 = [] if version == 29 else None
            huge = _obj("config", _leaf(0x01, "installdir", b"SafeDir\0", table2), table2)
            huge += _leaf(0x09, "hash", struct.pack("<I", 1_000_000), table2)
            kv_huge = bytes([0x00]) + _key("appinfo", table2) + huge
            self._write(_pack([(570, kv_huge), (730, _app_tree(table2, "ONLY730", "Next"))],
                              version, table2 if table2 is not None else []))
            self.assertEqual(short.appinfo(str(self.root), "730")["installdir"], "ONLY730", version)
            self.assertEqual(games._read_appinfo(str(self.root / "steamapps"))["730"]["installdir"], "ONLY730")

    def test_end_argument_stops_before_the_buffer(self):
        tree = _leaf(0x01, "installdir", b"SafeDir\0", None) + _leaf(0x01, "evil", b"ONLY-NEXT\0", None) + b"\x08"
        cut = tree.index(b"evil") - 1  # тип второго поля ещё не входит в границу
        for label, mod in (("shortcuts", short), ("games", games)):
            got, _, ok = mod._appinfo_kv(tree, 0, None, cut)
            self.assertFalse(ok, label)
            self.assertEqual(got, {"installdir": "SafeDir"}, label)
            self.assertNotIn("evil", got, label)


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
    def run_check(self, smi, app=""):
        d = Path(tempfile.mkdtemp(dir=TMP))
        (d / "S").mkdir()
        (d / "S/nvapi.off").touch()
        if app:
            (d / "S/play.app").write_text(app + "\n")
        script = f'''
S="{d}/S"
log() {{ echo "LOG $*"; }}
rd() {{ cat "$1" 2>/dev/null | head -1; }}
nvidia-smi() {{ printf '%b' {smi!r}; }}
NVAPI_OFF_APPS="312060 39210"
{fn("nvapi_check")}
nvapi_check
'''
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
        return (d / "S/nvapi.off").exists(), out

    def test_cases(self):
        one_rtx = "GPU 0: NVIDIA GeForce RTX 2070 (UUID: GPU-1)\n"
        two_rtx = "GPU 0: NVIDIA GeForce RTX 3060 (UUID: GPU-1)\nGPU 1: NVIDIA GeForce RTX 3060 (UUID: GPU-2)\n"
        gtx = "GPU 0: NVIDIA GeForce GTX 1650 (UUID: GPU-1)\nGPU 1: NVIDIA GeForce GTX 1650 (UUID: GPU-2)\n"
        self.assertFalse(self.run_check(one_rtx)[0])                       # RTX — NVAPI нужен для DLSS
        self.assertFalse(self.run_check(two_rtx)[0])                       # пара RTX — тоже (было: выключали — зря)
        self.assertFalse(self.run_check(one_rtx, "1245620")[0])            # другая игра
        off, out = self.run_check(one_rtx, "312060")                       # FF14 зависает с NVAPI и на RTX
        self.assertTrue(off)
        self.assertIn("игра 312060", out)
        self.assertTrue(self.run_check(one_rtx, "39210")[0])
        off, out = self.run_check(gtx)
        self.assertTrue(off)
        self.assertIn("нет RTX (NVIDIA GeForce GTX 1650)", out)

    def test_steam_started_without_nvapi(self):
        go = fn("steam_go")
        self.assertIn("PROTON_DISABLE_NVAPI=1", go)
        self.assertIn('[ -e "$S/nvapi.off" ]', go)
        boot = fn("launch_boot")
        self.assertIn("for a in $NVAPI_OFF_APPS", boot)                 # [v5.7] и у самой игры (.env)
        self.assertIn("PROTON_DISABLE_NVAPI=1 >", boot)
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

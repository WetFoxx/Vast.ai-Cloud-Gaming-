# wolf-setup.sh v4.4: большие архивы — частями (wolf-parts.py из heredoc). Настоящий код с настоящим rclone, «облако» —
# временная папка (rclone работает с ней так же, как с Drive). Размер части в тестах — 100 КБ.
# Главное: новая версия появляется только целиком; сбой посередине оставляет прежнюю целой; порча части ловится.

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "wolf-setup.sh").read_text()
CODE = re.search(r"^w /usr/local/bin/wolf-parts.py <<'PARTS'\n(.*?)^PARTS$", SRC, re.S | re.M).group(1)
WOLF = re.search(r"^w /usr/local/bin/wolf <<'WOLF'\n(.*?)^WOLF$", SRC, re.S | re.M).group(1)
RCLONE = shutil.which("rclone") or next((p for p in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone")
                                         if os.path.exists(p)), None)
PART = 100_000


@unittest.skipUnless(RCLONE, "нужен rclone")
class TestParts(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cloud, self.tmp, self.bin = self.d / "cloud", self.d / "tmp", self.d / "bin"
        for x in (self.cloud, self.tmp, self.bin):
            x.mkdir()
        self.script = self.d / "wolf-parts.py"
        self.script.write_text(CODE)
        self.fail_on = None                              # подстрока имени, на выгрузке которого rclone «падает»

    def env(self):
        path = str(Path(RCLONE).parent)
        if self.fail_on:
            # поддельный rclone: copyto в файл с этой подстрокой — ошибка, остальное — настоящему rclone
            fake = self.bin / "rclone"
            fake.write_text(f'#!/bin/bash\nfor a in "$@"; do case "$a" in *{self.fail_on}*) '
                            f'[[ " $* " == *" copyto "* ]] && exit 1 ;; esac; done\nexec "{RCLONE}" "$@"\n')
            fake.chmod(0o755)
            path = f"{self.bin}:{path}"
        return {"PATH": f"{path}:/usr/bin:/bin", "RCLONE_CONFIG": "/nonexistent", "WOLF_TMP": str(self.tmp),
                "WOLF_PART_BYTES": str(PART), "WOLF_PART_PAR": "3", "WOLF_PART_WAIT": "0", "HOME": str(self.d)}

    def run_p(self, *args, data=b""):
        return subprocess.run([sys.executable, str(self.script), *args], input=data, capture_output=True,
                              env=self.env())

    def put(self, obj, data):
        return self.run_p("put", str(self.cloud), obj, data=data).returncode

    def get(self, obj):
        r = self.run_p("get", str(self.cloud), obj)
        return r.returncode, r.stdout

    def files(self):
        return sorted(p.name for p in self.cloud.iterdir())

    def test_big_goes_in_parts_and_comes_back_exactly(self):
        data = os.urandom(PART * 3 + 1234)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        names = self.files()
        self.assertIn("g.tar.zst.parts", names)
        self.assertEqual(len([n for n in names if re.fullmatch(r"g\.tar\.zst\.g[0-9a-f]{8}-\d{4}", n)]), 4)
        self.assertNotIn("g.tar.zst", names)
        self.assertEqual(self.get("g.tar.zst"), (0, data))
        self.assertEqual(list(self.tmp.iterdir()), [])                # временные файлы убраны

    def test_small_stays_one_file_as_before(self):
        data = os.urandom(1000)
        self.assertEqual(self.put("s.tar.zst", data), 0)
        self.assertEqual(self.files(), ["s.tar.zst"])
        self.assertEqual((self.cloud / "s.tar.zst").read_bytes(), data)

    def test_new_version_replaces_old_parts(self):
        self.put("g.tar.zst", os.urandom(PART * 2 + 5))
        old = [n for n in self.files() if ".g" in n]
        data = os.urandom(PART * 2 + 7)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        self.assertFalse(set(old) & set(self.files()))                # старое поколение удалено
        self.assertEqual(self.get("g.tar.zst"), (0, data))

    def test_once_in_parts_always_in_parts(self):
        self.put("g.tar.zst", os.urandom(PART * 2))
        small = os.urandom(10)
        self.assertEqual(self.put("g.tar.zst", small), 0)
        self.assertNotIn("g.tar.zst", self.files())                   # не одиночным файлом рядом с оглавлением
        self.assertEqual(self.get("g.tar.zst"), (0, small))

    def test_single_grows_into_parts_single_removed(self):
        self.put("g.tar.zst", os.urandom(100))
        data = os.urandom(PART * 2 + 1)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        self.assertNotIn("g.tar.zst", self.files())
        self.assertEqual(self.get("g.tar.zst"), (0, data))

    def test_failure_midway_keeps_previous_version(self):
        prev = os.urandom(PART * 2 + 3)
        self.put("g.tar.zst", prev)
        before = self.files()
        self.fail_on = "-0002"                                        # третья часть нового поколения не выгрузится
        self.assertEqual(self.put("g.tar.zst", os.urandom(PART * 4)), 1)
        self.fail_on = None
        self.assertEqual(self.files(), before)                        # ни новых частей, ни нового оглавления
        self.assertEqual(self.get("g.tar.zst"), (0, prev))

    def test_damaged_part_is_caught(self):
        self.put("g.tar.zst", os.urandom(PART * 2 + 9))
        part = next(p for p in self.cloud.iterdir() if p.name.endswith("-0001"))
        part.write_bytes(os.urandom(part.stat().st_size))            # тот же размер, другое содержимое
        rc, _ = self.get("g.tar.zst")
        self.assertEqual(rc, 1)

    def test_rm_removes_every_form_but_not_neighbours(self):
        self.put("g.tar.zst", os.urandom(PART * 2))
        self.put("g.tar.zst.1a2b3c4d", os.urandom(PART * 2))          # дельта к этой базе — отдельный объект
        (self.cloud / "g.tar.zst.parts.new").write_text("хвост")
        (self.cloud / "g.tar.zst.gdeadbeef-0000").write_text("сирота")
        self.assertEqual(self.run_p("rm", str(self.cloud), "g.tar.zst").returncode, 0)
        left = self.files()
        self.assertFalse([n for n in left if not n.startswith("g.tar.zst.1a2b3c4d")], left)
        self.assertIn("g.tar.zst.1a2b3c4d.parts", left)


class TestWolfFunctions(unittest.TestCase):
    """rver и names из wolf: версия объекта при одиночном файле, частях и дельтах обоих видов."""

    def fn(self, name):
        # многострочная функция кончается строкой «}», однострочная — в той же строке
        m = (re.search(rf"^{name}\(\) *\{{\n.*?\n\}}$", WOLF, re.S | re.M)
             or re.search(rf"^{name}\(\) *\{{[^\n]*\}}$", WOLF, re.M))
        return m.group(0)

    def sh(self, listing, cmd):
        f = Path(tempfile.mkdtemp()) / "ls"
        f.write_text("".join("\t".join(r) + "\n" for r in listing))
        r = subprocess.run(["bash", "-c", f'LS="{f}"\n{self.fn("rver")}\n{self.fn("names")}\n{cmd}'],
                           capture_output=True, text=True)
        return r.stdout.strip()

    def test_versions(self):
        single = [("a.tar.zst", "11112222aaaa", "5")]
        chunked = [("a.tar.zst.parts", "33334444bbbb", "1"), ("a.tar.zst.gabcdef01-0000", "x", "9")]
        self.assertEqual(self.sh(single, "rver a"), "11112222aaaa")
        self.assertEqual(self.sh(chunked, "rver a"), "33334444bbbb")
        self.assertEqual(self.sh(single + chunked, "rver a"), "33334444bbbb")       # при обоих — оглавление
        self.assertEqual(self.sh(chunked + [("a.tar.zst.33334444", "dd", "1")], "rver a"), "33334444bbbb+dd")
        self.assertEqual(self.sh(chunked + [("a.tar.zst.33334444.parts", "ee", "1")], "rver a"), "33334444bbbb+ee")
        self.assertEqual(self.sh([], "rver a"), "")

    def test_names(self):
        rows = [("sgame--X.tar.zst.parts", "h", "1"), ("sgame--X.tar.zst.g0123abcd-0000", "h", "1"),
                ("sgame--Y.tar.zst", "h", "1"), ("sgame--Y.tar.zst.11112222", "h", "1"),
                ("sgame--Z.tar.zst", "h", "1"), ("sgame--Z.tar.zst.parts", "h", "1"), ("identity.tar.zst", "h", "1")]
        self.assertEqual(self.sh(rows, "names '^sgame--'").split("\n"), ["sgame--X", "sgame--Y", "sgame--Z"])


if __name__ == "__main__":
    unittest.main()


class TestMacListing(unittest.TestCase):
    """Приложение на Mac: игра из частей — одна строка «Игры в облаке» с полным размером, «Убрать» — все её файлы."""

    def test_parts_grouped_with_archive(self):
        import json
        from unittest import mock
        sys.path.insert(0, str(ROOT))
        import drive
        listing = [{"Name": "sgame--X.tar.zst.parts", "Size": 300, "ModTime": "2026-09-26T10:00:00Z"},
                   {"Name": "sgame--X.tar.zst.g0123abcd-0000", "Size": 1000, "ModTime": "2026-09-26T10:00:00Z"},
                   {"Name": "sgame--X.tar.zst.g0123abcd-0001", "Size": 500, "ModTime": "2026-09-26T10:01:00Z"},
                   {"Name": "identity.tar.zst", "Size": 7, "ModTime": "2026-09-25T10:00:00Z"}]
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(listing), stderr="")
        with mock.patch.object(drive, "rclone", lambda *a, **k: done):
            a = drive.archives()
        self.assertEqual(a["sgame--X"]["size"], 1800)
        self.assertEqual(len(a["sgame--X"]["files"]), 3)
        self.assertEqual(set(a), {"sgame--X", "identity"})

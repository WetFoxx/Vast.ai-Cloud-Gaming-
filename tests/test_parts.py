# wolf-setup.sh v4.4: большие архивы — частями (wolf-parts.py из heredoc). Настоящий код с настоящим rclone, «облако» —
# временная папка (rclone работает с ней так же, как с Drive). Размер части в тестах — 100 КБ.
# Главное: новая версия появляется только целиком; сбой посередине оставляет прежнюю целой; порча части ловится;
# мало места на диске — прямо из потока, как до v4.4; части на диске у всех процессов вместе — не больше мест.

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "wolf-setup.sh").read_text()
CODE = re.search(r"^w /usr/local/bin/wolf-parts.py <<'PARTS'\n(.*?)^PARTS$", SRC, re.S | re.M).group(1)
WOLF = re.search(r"^w /usr/local/bin/wolf <<'WOLF'\n(.*?)^WOLF$", SRC, re.S | re.M).group(1)
RCLONE = shutil.which("rclone") or next((p for p in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone")
                                         if os.path.exists(p)), None)
PART = 100_000
RANGE = 30_000          # кусок скачивания — нарочно не делит часть ровно: у каждой части короткий последний кусок
ZSTD = shutil.which("zstd") or next((p for p in ("/opt/homebrew/bin/zstd", "/usr/local/bin/zstd")
                                     if os.path.exists(p)), None)


class PartsBase(unittest.TestCase):
    """Настоящий wolf-parts.py с настоящим rclone; «облако» — папка cloud, временные файлы — tmp."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cloud, self.tmp, self.bin = self.d / "cloud", self.d / "tmp", self.d / "bin"
        for x in (self.cloud, self.tmp, self.bin):
            x.mkdir()
        self.script = self.d / "wolf-parts.py"
        self.script.write_text(CODE)
        self.fake_sh = None                              # поддельный rclone (см. fake)
        self.extra = {}                                  # добавочные переменные окружения

    def fake(self, sub, cmd, done=False):
        """rclone «падает» на команде cmd, если в аргументах есть sub; done — сначала всё же делает (ответ потерялся)."""
        act = f'"{RCLONE}" "$@"; exit 1' if done else "exit 1"
        self.fake_sh = f'for a in "$@"; do case "$a" in *{sub}*) [[ " $* " == *" {cmd} "* ]] && {{ {act}; }} ;; esac; done'

    def fail_active_index(self, code):
        """moveto оглавления не делается. md5sum боевого ОБЪЕКТ.parts завершается code; .parts.new не трогаем."""
        self.fake_sh = f'''
if [[ " $* " == *" moveto "* && " $* " == *".parts.new"* ]]; then exit 1; fi
for a in "$@"; do
  case "$a" in
    *.parts.new) ;;
    *.parts) [[ " $* " == *" md5sum "* ]] && exit {int(code)} ;;
  esac
done
'''

    def lost_parts_moveto(self, happened):
        """moveto .parts.new отвечает ошибкой. happened — переименование всё же делается.
        Следующие md5sum тоже падают: результат публикации неизвестен."""
        do = f'"{RCLONE}" "$@"' if happened else ":"
        mark = self.d / "moved"
        self.fake_sh = (
            f'mark="{mark}"\n'
            'if [[ " $* " == *" moveto "* && " $* " == *".parts.new"* ]]; then\n'
            f'  {do}\n'
            '  echo 1 > "$mark"\n'
            '  exit 1\n'
            'fi\n'
            'if [[ " $* " == *" md5sum "* && -f "$mark" ]]; then exit 1; fi\n'
        )

    def env(self):
        path = str(Path(RCLONE).parent)
        if self.fake_sh:
            fake = self.bin / "rclone"
            fake.write_text(f'#!/bin/bash\n{self.fake_sh}\nexec "{RCLONE}" "$@"\n')
            fake.chmod(0o755)
            path = f"{self.bin}:{path}"
        return {"PATH": f"{path}:/usr/bin:/bin", "RCLONE_CONFIG": "/nonexistent", "WOLF_TMP": str(self.tmp),
                "WOLF_PART_BYTES": str(PART), "WOLF_PART_PAR": "3", "WOLF_PART_WAIT": "0", "HOME": str(self.d),
                "WOLF_RANGE_BYTES": str(RANGE), "WOLF_RANGE_PAR": "3",
                **self.extra}

    def run_p(self, *args, data=b""):
        return subprocess.run([sys.executable, str(self.script), *args], input=data, capture_output=True,
                              env=self.env())

    def leftovers(self):
        """Временные файлы, кроме замков мест (.slotN, .ramN — постоянные)."""
        return [p.name for p in self.tmp.iterdir() if not p.name.startswith((".slot", ".ram"))]

    def chunked(self, obj="g.tar.zst"):
        return [n for n in self.files() if re.fullmatch(re.escape(obj) + r"\.g[0-9a-f]{8}-\d{4}", n)]

    def put(self, obj, data):
        return self.run_p("put", str(self.cloud), obj, data=data).returncode

    def get(self, obj):
        r = self.run_p("get", str(self.cloud), obj)
        return r.returncode, r.stdout

    def files(self):
        return sorted(p.name for p in self.cloud.iterdir())


@unittest.skipUnless(RCLONE, "нужен rclone")
class TestParts(PartsBase):
    def test_big_goes_in_parts_and_comes_back_exactly(self):
        data = os.urandom(PART * 3 + 1234)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        names = self.files()
        self.assertIn("g.tar.zst.parts", names)
        self.assertEqual(len([n for n in names if re.fullmatch(r"g\.tar\.zst\.g[0-9a-f]{8}-\d{4}", n)]), 4)
        self.assertNotIn("g.tar.zst", names)
        self.assertEqual(self.get("g.tar.zst"), (0, data))
        self.assertEqual(self.leftovers(), [])                        # временные файлы убраны

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
        self.fake("-0002", "copyto")                                  # третья часть нового поколения не выгрузится
        self.assertEqual(self.put("g.tar.zst", os.urandom(PART * 4)), 1)
        self.fake_sh = None
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

    def test_rm_all_takes_deltas_too_but_not_neighbours(self):
        self.put("g.tar.zst", os.urandom(PART * 2))
        self.put("g.tar.zst.1a2b3c4d", os.urandom(PART * 2))          # дельта частями
        self.put("g.tar.zst.1a2b3c4d.x", os.urandom(10))
        self.put("g2.tar.zst", os.urandom(PART * 2))                  # соседняя игра с похожим именем
        self.assertEqual(self.run_p("rm", str(self.cloud), "g.tar.zst", "--all").returncode, 0)
        self.assertFalse([n for n in self.files() if not n.startswith("g2.tar.zst")], self.files())
        self.assertEqual(self.get("g2.tar.zst")[0], 0)

    def test_rm_keeps_parts_when_index_cannot_be_removed(self):
        self.put("g.tar.zst", os.urandom(PART * 2))
        before = self.files()
        self.fake(".parts", "deletefile")                             # оглавление не удаляется
        self.assertEqual(self.run_p("rm", str(self.cloud), "g.tar.zst").returncode, 1)
        self.assertEqual(self.files(), before)                        # части целы — архив по-прежнему читается
        self.fake_sh = None
        self.assertEqual(self.get("g.tar.zst")[0], 0)

    def test_index_not_written_keeps_previous_version(self):
        prev = os.urandom(PART * 2 + 3)
        self.put("g.tar.zst", prev)
        before = self.files()
        self.fake(".parts.new", "copyto")
        self.assertEqual(self.put("g.tar.zst", os.urandom(PART * 3)), 1)
        self.fake_sh = None
        self.assertEqual(self.files(), before)
        self.assertEqual(self.get("g.tar.zst"), (0, prev))

    def test_commit_done_but_reported_failed_is_kept(self):
        # оглавление переименовалось, а rclone ответил ошибкой (потерялся ответ сервера): новые части не удалять
        self.put("g.tar.zst", os.urandom(PART * 2))
        data = os.urandom(PART * 3 + 11)
        self.fake(".parts.new", "moveto", done=True)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        self.fake_sh = None
        self.assertEqual(self.get("g.tar.zst"), (0, data))
        self.assertEqual(len(self.chunked()), 4)                      # старое поколение убрано, новое целое

    def test_single_rename_done_but_reported_failed_is_kept(self):
        data = os.urandom(500)
        self.fake(".part", "moveto", done=True)
        self.assertEqual(self.put("s.tar.zst", data), 0)
        self.fake_sh = None
        self.assertEqual((self.cloud / "s.tar.zst").read_bytes(), data)

    def test_stage_stays_inactive_until_publish(self):
        prev = os.urandom(1000)
        self.put("s.tar.zst", prev)
        new = os.urandom(900)
        staged = self.run_p("stage", str(self.cloud), "s.tar.zst", data=new)
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.assertTrue(staged.stdout.startswith(b"vastgame-stage 1\n"))
        self.assertEqual((self.cloud / "s.tar.zst").read_bytes(), prev)
        self.assertTrue((self.cloud / "s.tar.zst.part").is_file())
        published = self.run_p("publish", str(self.cloud), "s.tar.zst", data=staged.stdout)
        self.assertEqual(published.returncode, 0, published.stderr)
        self.assertEqual((self.cloud / "s.tar.zst").read_bytes(), new)
        self.assertFalse((self.cloud / "s.tar.zst.part").exists())

    def tamper_lsjson(self, mode):
        """Ответы lsjson: error — код 1; size/md5/missing — подмена JSON настоящего rclone."""
        script = self.d / "tamper-lsjson.py"
        script.write_text(
            "import json, sys\n"
            "item = json.loads(sys.stdin.read())\n"
            "mode = sys.argv[1]\n"
            "if mode == 'size':\n"
            "    item['Size'] = int(item['Size']) + 1\n"
            "elif mode == 'md5':\n"
            "    hashes = item.setdefault('Hashes', {})\n"
            "    key = next((k for k in hashes if str(k).lower() == 'md5'), 'md5')\n"
            "    hashes[key] = '0' * 32\n"
            "elif mode == 'missing':\n"
            "    hashes = item.get('Hashes') or {}\n"
            "    for key in list(hashes):\n"
            "        if str(key).lower() == 'md5':\n"
            "            del hashes[key]\n"
            "    item['Hashes'] = hashes\n"
            "json.dump(item, sys.stdout)\n")
        self.fake_sh = (
            'if [[ " $* " == *" lsjson "* ]]; then\n'
            f'  if [ "{mode}" = error ]; then exit 1; fi\n'
            f'  out=$("{RCLONE}" "$@" 2>"{self.d}/lsjson.err") || exit $?\n'
            f'  printf %s "$out" | "{sys.executable}" "{script}" "{mode}"\n'
            '  exit $?\n'
            'fi\n'
        )

    def test_both_size_and_md5_checks_run_before_rename(self):
        log = self.d / "calls"
        self.fake_sh = f'printf "%s\\n" "$*" >> "{log}"\n'
        data = os.urandom(400)
        self.assertEqual(self.put("s.tar.zst", data), 0)
        lines = log.read_text().splitlines()
        lsjson = [i for i, line in enumerate(lines) if " lsjson " in f" {line} "]
        moveto = [i for i, line in enumerate(lines) if " moveto " in f" {line} "]
        self.assertEqual(len(lsjson), 2)
        self.assertLess(lsjson[0], lsjson[1])
        self.assertLess(lsjson[1], moveto[0])
        self.assertFalse(any(" md5sum " in f" {line} " or " lsl " in f" {line} " for line in lines))

    def test_bad_lsjson_before_rename_keeps_the_previous_archive(self):
        prev = os.urandom(1000)
        self.put("s.tar.zst", prev)
        for mode in ("size", "md5", "missing", "error"):
            with self.subTest(mode=mode):
                staged = self.run_p("stage", str(self.cloud), "s.tar.zst", data=os.urandom(800))
                self.assertEqual(staged.returncode, 0, staged.stderr)
                self.tamper_lsjson(mode)
                published = self.run_p("publish", str(self.cloud), "s.tar.zst", data=staged.stdout)
                self.assertEqual(published.returncode, 1, published.stderr)
                self.fake_sh = None
                self.assertEqual((self.cloud / "s.tar.zst").read_bytes(), prev)
                self.assertFalse((self.cloud / "s.tar.zst.part").exists())

    def test_lsjson_error_after_prepare_does_not_replace_the_archive(self):
        prev = os.urandom(1000)
        self.put("s.tar.zst", prev)
        self.tamper_lsjson("error")
        staged = self.run_p("stage", str(self.cloud), "s.tar.zst", data=os.urandom(700))
        self.assertEqual(staged.returncode, 1, staged.stderr)
        self.fake_sh = None
        self.assertEqual((self.cloud / "s.tar.zst").read_bytes(), prev)
        self.assertFalse((self.cloud / "s.tar.zst.part").exists())

    def test_bad_lsjson_keeps_previous_parts_archive(self):
        prev = os.urandom(PART * 2 + 5)
        self.put("g.tar.zst", prev)
        staged = self.run_p("stage", str(self.cloud), "g.tar.zst", data=os.urandom(PART * 2 + 9))
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.tamper_lsjson("size")
        published = self.run_p("publish", str(self.cloud), "g.tar.zst", data=staged.stdout)
        self.assertEqual(published.returncode, 1, published.stderr)
        self.fake_sh = None
        self.assertEqual(self.get("g.tar.zst"), (0, prev))

    def test_publish_rejects_a_corrupt_staged_file(self):
        prev = os.urandom(1000)
        self.put("s.tar.zst", prev)
        staged = self.run_p("stage", str(self.cloud), "s.tar.zst", data=os.urandom(800))
        self.assertEqual(staged.returncode, 0, staged.stderr)
        part = self.cloud / "s.tar.zst.part"
        blob = bytearray(part.read_bytes())
        blob[0] ^= 0xff
        part.write_bytes(blob)
        published = self.run_p("publish", str(self.cloud), "s.tar.zst", data=staged.stdout)
        self.assertEqual(published.returncode, 1, published.stderr)
        self.assertEqual((self.cloud / "s.tar.zst").read_bytes(), prev)

    def test_abort_drops_prepared_parts_and_keeps_the_old_ones(self):
        prev = os.urandom(PART * 2 + 3)
        self.put("g.tar.zst", prev)
        before = self.files()
        staged = self.run_p("stage", str(self.cloud), "g.tar.zst", data=os.urandom(PART * 2 + 9))
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.assertEqual(self.get("g.tar.zst"), (0, prev))          # боевое оглавление ещё старое
        self.assertNotEqual(self.files(), before)
        self.assertEqual(self.run_p("abort", str(self.cloud), "g.tar.zst", data=staged.stdout).returncode, 0)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.get("g.tar.zst"), (0, prev))

    def _stage_next(self, prev, new):
        self.put("g.tar.zst", prev)
        staged = self.run_p("stage", str(self.cloud), "g.tar.zst", data=new)
        self.assertEqual(staged.returncode, 0, staged.stderr)
        return staged.stdout

    def test_unknown_moveto_keeps_parts_when_rename_happened(self):
        prev, new = os.urandom(PART * 2 + 3), os.urandom(PART * 2 + 8)
        receipt = self._stage_next(prev, new)
        self.lost_parts_moveto(True)
        published = self.run_p("publish", str(self.cloud), "g.tar.zst", data=receipt)
        self.assertEqual(published.returncode, 2, published.stderr)
        self.assertIn("не подтверждена", published.stderr.decode())
        aborted = self.run_p("abort", str(self.cloud), "g.tar.zst", data=receipt)
        self.assertEqual(aborted.returncode, 2, aborted.stderr)
        self.assertIn("части оставляю", aborted.stderr.decode())
        self.assertTrue(list(self.tmp.glob("attempt-*")))
        self.fake_sh = None
        self.assertEqual(self.get("g.tar.zst"), (0, new))
        resumed = self.run_p("resume", str(self.cloud), "g.tar.zst", data=receipt)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.get("g.tar.zst"), (0, new))

    def test_unknown_moveto_keeps_parts_when_rename_did_not_happen(self):
        prev, new = os.urandom(PART * 2 + 4), os.urandom(PART * 2 + 6)
        receipt = self._stage_next(prev, new)
        self.lost_parts_moveto(False)
        published = self.run_p("publish", str(self.cloud), "g.tar.zst", data=receipt)
        self.assertEqual(published.returncode, 2, published.stderr)
        aborted = self.run_p("abort", str(self.cloud), "g.tar.zst", data=receipt)
        self.assertEqual(aborted.returncode, 2, aborted.stderr)
        self.assertTrue(any(name.endswith(".parts.new") for name in self.files()))
        self.fake_sh = None
        self.assertEqual(self.get("g.tar.zst"), (0, prev))
        resumed = self.run_p("resume", str(self.cloud), "g.tar.zst", data=receipt)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.get("g.tar.zst"), (0, new))
        self.assertFalse(any(name.endswith(".parts.new") for name in self.files()))

    def test_absent_parts_index_is_not_a_failed_request(self):
        single, new = os.urandom(1000), os.urandom(PART * 2 + 40)
        self.put("g.tar.zst", single)
        staged = self.run_p("stage", str(self.cloud), "g.tar.zst", data=new)
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.fail_active_index(1)
        published = self.run_p("publish", str(self.cloud), "g.tar.zst", data=staged.stdout)
        self.assertEqual(published.returncode, 2, published.stderr)
        aborted = self.run_p("abort", str(self.cloud), "g.tar.zst", data=staged.stdout)
        self.assertEqual(aborted.returncode, 2, aborted.stderr)
        self.assertEqual((self.cloud / "g.tar.zst").read_bytes(), single)
        self.assertTrue((self.cloud / "g.tar.zst.parts.new").is_file())
        self.assertEqual(self.run_p("resume", str(self.cloud), "g.tar.zst", data=staged.stdout).returncode, 2)
        self.fake_sh = None
        self.assertEqual((self.cloud / "g.tar.zst").read_bytes(), single)
        resumed = self.run_p("resume", str(self.cloud), "g.tar.zst", data=staged.stdout)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn("активного оглавления нет", resumed.stderr.decode())
        self.assertEqual(self.get("g.tar.zst"), (0, new))

    def test_confirmed_moveto_refusal_drops_only_the_staging(self):
        prev = os.urandom(PART * 2 + 5)
        receipt = self._stage_next(prev, os.urandom(PART * 2 + 7))
        self.fake(".parts.new", "moveto")
        published = self.run_p("publish", str(self.cloud), "g.tar.zst", data=receipt)
        self.assertEqual(published.returncode, 1, published.stderr)
        self.fake_sh = None
        self.assertNotIn("g.tar.zst.parts.new", self.files())
        self.assertEqual(self.get("g.tar.zst"), (0, prev))

    def test_publish_rejects_a_corrupt_part(self):
        prev = os.urandom(PART * 2 + 4)
        self.put("g.tar.zst", prev)
        before = set(self.files())
        staged = self.run_p("stage", str(self.cloud), "g.tar.zst", data=os.urandom(PART * 2 + 6))
        self.assertEqual(staged.returncode, 0, staged.stderr)
        fresh = next(p for p in self.cloud.iterdir() if p.name not in before and p.name.endswith("-0000"))
        blob = bytearray(fresh.read_bytes())
        blob[0] ^= 0xff
        fresh.write_bytes(blob)
        published = self.run_p("publish", str(self.cloud), "g.tar.zst", data=staged.stdout)
        self.assertEqual(published.returncode, 1, published.stderr)
        self.assertEqual(self.get("g.tar.zst"), (0, prev))

    def test_missing_folder_is_created(self):
        self.cloud = self.cloud / "новая папка"
        data = os.urandom(PART * 2 + 1)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        self.assertEqual(self.get("g.tar.zst"), (0, data))


@unittest.skipUnless(RCLONE, "нужен rclone")
class TestProgress(PartsBase):
    """v4.6: ход для приложения — файл WOLF_PG «сделано всего скорость время» при скачивании и выгрузке."""

    def progress(self):
        done, total, speed, ts = (self.d / "pg").read_text().split()
        return int(done), int(total), int(speed), int(ts)

    def test_download_progress(self):
        data = os.urandom(PART * 3 + 11)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        self.extra = {"WOLF_PG": str(self.d / "pg")}
        code, out = self.get("g.tar.zst")
        self.assertEqual((code, out), (0, data))                          # данные не пострадали
        done, total, speed, ts = self.progress()
        self.assertEqual((done, total), (len(data), len(data)))           # в конце — всё

    def test_count_passes_data_and_writes_progress(self):
        data = os.urandom(3 * 1024 * 1024 + 5)
        self.extra = {"WOLF_PG": str(self.d / "pg"), "WOLF_PG_TOTAL": str(len(data))}
        r = self.run_p("count", data=data)
        self.assertEqual((r.returncode, r.stdout), (0, data))
        self.assertEqual(self.progress()[:2], (len(data), len(data)))

    def test_without_pg_nothing_written(self):
        r = self.run_p("count", data=b"abc")
        self.assertEqual(r.stdout, b"abc")
        self.assertFalse((self.d / "pg").exists())


@unittest.skipUnless(RCLONE, "нужен rclone")
class TestRangedRead(PartsBase):
    """v4.5: одиночный файл (архив до v4.4) — кусками в несколько потоков, прямо в stdout, MD5 сверяется."""

    def single(self, data, name="s.tar.zst"):
        (self.cloud / name).write_bytes(data)
        return name, str(len(data)), hashlib.md5(data).hexdigest()

    def cat(self, name, size, md5):
        r = self.run_p("cat", str(self.cloud), name, size, md5)
        return r.returncode, r.stdout

    def test_single_file_in_ranges(self):
        for n in (RANGE * 3 + 7, RANGE * 3, 1, 0):                   # с хвостом, ровно, один байт, пустой
            data = os.urandom(n)
            self.assertEqual(self.cat(*self.single(data)), (0, data), n)
        self.assertEqual(self.leftovers(), [])

    def test_wrong_md5_is_an_error(self):
        name, size, _ = self.single(os.urandom(RANGE * 2 + 1))
        self.assertEqual(self.cat(name, size, "0" * 32)[0], 1)
        name, size, _ = self.single(b"", "e.tar.zst")                # пустой — кусков нет, но сверка есть
        self.assertEqual(self.cat(name, size, "0" * 32)[0], 1)

    def test_trickling_range_is_cut_and_retried(self):
        # [2026-10-15] кусок идёт по капле (данные есть — rclone не считает зависанием): срок вышел — заново
        data = os.urandom(RANGE * 2 + 5)
        mark = self.d / "slow-once"
        self.fake_sh = (f'[[ " $* " == *" cat "* && " $* " == *"--offset=0 "* && ! -e "{mark}" ]] '
                        f'&& {{ : > "{mark}"; printf x; exec sleep 30; }}')   # один процесс, как rclone
        self.extra.update({"WOLF_SLOW_MIN_SEC": "1", "WOLF_SLOW_KBPS": "100000"})
        t = time.time()
        self.assertEqual(self.cat(*self.single(data)), (0, data))
        self.assertTrue(mark.exists())
        self.assertLess(time.time() - t, 20)                           # не ждали 30 с медленного куска

    def test_failed_range_is_retried(self):
        data = os.urandom(RANGE * 3 + 5)
        mark = self.d / "failed-once"
        # первый запрос куска со смещением RANGE падает, повтор — удачный
        self.fake_sh = (f'[[ " $* " == *" cat "* && " $* " == *"--offset={RANGE} "* && ! -e "{mark}" ]] '
                        f'&& {{ : > "{mark}"; exit 1; }}')
        self.assertEqual(self.cat(*self.single(data)), (0, data))
        self.assertTrue(mark.exists())

    def test_range_that_keeps_failing_is_an_error(self):
        self.fake_sh = f'[[ " $* " == *" cat "* && " $* " == *"--offset={RANGE * 2} "* ]] && exit 1'
        self.assertEqual(self.cat(*self.single(os.urandom(RANGE * 3)))[0], 1)

    def test_short_file_is_an_error(self):
        name, size, md5 = self.single(os.urandom(RANGE * 2))
        (self.cloud / name).write_bytes(os.urandom(RANGE))           # в облаке короче, чем в списке
        self.assertEqual(self.cat(name, size, md5)[0], 1)


@unittest.skipUnless(RCLONE and ZSTD, "нужны rclone и zstd")
class TestWolfGet(PartsBase):
    """get из wolf (как есть, из heredoc): частями → parts get; большой одиночный → parts cat (кусками);
    маленький одиночный → rclone cat одним потоком, как раньше. Распаковка подменена записью в файл."""

    BIG = 50_000                                                      # WOLF_BIG_FILE для теста

    def z(self, data):
        return subprocess.run([ZSTD, "-q", "-c"], input=data, capture_output=True, check=True).stdout

    def get_wolf(self, obj):
        fn = re.search(r"^get\(\) \{.*?^\}$", WOLF, re.S | re.M).group(0)
        ls, out, log = self.d / "ls", self.d / "out", self.d / "parts.log"
        ls.write_bytes(subprocess.run([RCLONE, "lsf", "--files-only", "-F", "phs", "--hash", "MD5", "-s", "\t",
                                       str(self.cloud)], capture_output=True, check=True,
                                      env={**self.env(), "PATH": os.environ["PATH"]}).stdout)
        script = (f'LS="{ls}"; R="{self.cloud}"; T="{self.tmp}"\n'
                  'rc() { rclone "$@" --retries=1; }\n'
                  f'parts() {{ local c=$1; shift; python3 "{self.script}" "$c" "$R" "$@" 2>>"{log}"; }}\n'
                  f'xt_user() {{ cat > "{out}"; }}\n'
                  f'{fn}\nget "{obj}" name && echo ГОТОВО || echo СБОЙ\n')
        env = {**self.env(), "WOLF_BIG_FILE": str(self.BIG), "PATH": f"{Path(ZSTD).parent}:{self.env()['PATH']}"}
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        used_parts = log.exists() and obj in log.read_text()
        return r.stdout.strip(), out.read_bytes() if out.exists() else b"", used_parts

    def test_chunked(self):
        data = os.urandom(PART * 2 + 99)
        self.assertEqual(self.put("g.tar.zst", self.z(data)), 0)
        self.assertEqual(self.get_wolf("g.tar.zst"), ("ГОТОВО", data, True))

    def test_big_single_goes_in_ranges(self):
        data = os.urandom(self.BIG * 2)
        (self.cloud / "b.tar.zst").write_bytes(self.z(data))           # как архив, выгруженный до v4.4
        self.assertEqual(self.get_wolf("b.tar.zst"), ("ГОТОВО", data, True))

    def test_small_single_as_before(self):
        data = os.urandom(1000)
        (self.cloud / "s.tar.zst").write_bytes(self.z(data))
        self.assertEqual(self.get_wolf("s.tar.zst"), ("ГОТОВО", data, False))

    def test_big_single_damaged_fails(self):
        data = os.urandom(self.BIG * 2)
        (self.cloud / "b.tar.zst").write_bytes(self.z(data))
        fn = self.get_wolf                                            # список снят, потом файл в облаке подменили
        real = subprocess.run
        def swap(*a, **k):
            r = real(*a, **k)
            if a and a[0][:2] == [RCLONE, "lsf"]:
                (self.cloud / "b.tar.zst").write_bytes(self.z(os.urandom(self.BIG * 2)))
            return r
        from unittest import mock
        with mock.patch.object(subprocess, "run", swap):
            self.assertEqual(fn("b.tar.zst")[0], "СБОЙ")


@unittest.skipUnless(RCLONE, "нужен rclone")
class TestPartsLowDisk(PartsBase):
    """Мало места на диске (свободно меньше WOLF_PART_MINFREE): без временных файлов, прямо из потока, как до v4.4."""

    LOW = {"WOLF_PART_MINFREE": str(10 ** 18)}

    def test_new_archive_goes_as_one_file_like_before(self):
        self.extra = self.LOW
        data = os.urandom(PART * 3 + 17)
        self.assertEqual(self.put("g.tar.zst", data), 0)
        self.assertEqual(self.files(), ["g.tar.zst"])                 # одним файлом, частей нет
        self.assertEqual((self.cloud / "g.tar.zst").read_bytes(), data)
        self.assertEqual(self.leftovers(), [])

    def test_chunked_archive_stays_chunked_and_reads_back(self):
        self.put("g.tar.zst", os.urandom(PART * 2))                   # места было много — частями
        self.extra = self.LOW
        data = os.urandom(PART * 3 + 5)
        self.assertEqual(self.put("g.tar.zst", data), 0)              # части — прямо из потока
        self.assertEqual(len(self.chunked()), 4)
        self.assertNotIn("g.tar.zst", self.files())
        self.assertEqual(self.get("g.tar.zst"), (0, data))            # и скачивание — прямо в распаковку
        self.extra = {}
        self.assertEqual(self.get("g.tar.zst"), (0, data))            # и обычным путём — то же самое

    def test_exact_multiple_of_part_size(self):
        self.put("g.tar.zst", os.urandom(PART * 2))
        self.extra = self.LOW
        data = os.urandom(PART * 2)                                   # поток кончается ровно на границе части
        self.assertEqual(self.put("g.tar.zst", data), 0)
        self.assertEqual(len(self.chunked()), 2)                      # без пустой третьей части
        self.assertEqual(self.get("g.tar.zst"), (0, data))

    def test_damaged_part_is_caught_when_streaming(self):
        self.put("g.tar.zst", os.urandom(PART * 2 + 9))
        part = next(p for p in self.cloud.iterdir() if p.name.endswith("-0000"))
        part.write_bytes(os.urandom(part.stat().st_size))
        self.extra = self.LOW
        self.assertEqual(self.get("g.tar.zst")[0], 1)

    def test_stream_failure_keeps_previous_version(self):
        prev = os.urandom(PART * 2 + 1)
        self.put("g.tar.zst", prev)
        before = self.files()
        self.extra = self.LOW
        self.fake("-0001", "rcat")
        self.assertEqual(self.put("g.tar.zst", os.urandom(PART * 3)), 1)
        self.fake_sh = None
        self.assertEqual(self.files(), before)
        self.assertEqual(self.get("g.tar.zst"), (0, prev))


@unittest.skipUnless(RCLONE, "нужен rclone")
class TestSlots(PartsBase):
    """Места под части на диске общие для всех процессов: заняты — ждём, а не переполняем диск."""

    def test_two_archives_share_one_slot(self):
        self.extra = {"WOLF_PART_SLOTS": "1"}
        data = {o: os.urandom(PART * 4 + 3) for o in ("a.tar.zst", "b.tar.zst")}
        res = {}
        th = [threading.Thread(target=lambda o=o: res.__setitem__(o, self.put(o, data[o]))) for o in data]
        for t in th:
            t.start()
        for t in th:
            t.join(120)
        self.assertEqual(res, {"a.tar.zst": 0, "b.tar.zst": 0})
        for o in data:
            self.assertEqual(self.get(o), (0, data[o]))

    def test_get_waits_for_a_free_slot(self):
        import fcntl
        data = os.urandom(PART * 3)
        self.put("g.tar.zst", data)
        self.extra = {"WOLF_RANGE_SLOTS": "1"}
        # вывод — в файл, не в канал: переполненный канал сам остановил бы процесс, и тест ничего бы не проверял
        with open(self.tmp / ".ram0", "a") as held, open(self.d / "out", "wb") as out:
            fcntl.flock(held, fcntl.LOCK_EX)                          # единственное место в памяти занято «другим»
            p = subprocess.Popen([sys.executable, str(self.script), "get", str(self.cloud), "g.tar.zst"],
                                 stdout=out, stderr=subprocess.DEVNULL, env=self.env())
            for _ in range(30):                                       # без ожидания управился бы за доли секунды
                time.sleep(0.1)
                self.assertIsNone(p.poll(), "скачал, не дождавшись места")
        self.assertEqual(p.wait(timeout=60), 0)                       # место освободилось — докачал
        self.assertEqual((self.d / "out").read_bytes(), data)
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

    def unpack_run(self, fails):
        """[v5.8] unpack с поддельным get: первые fails скачиваний «портятся» (как «не сходится MD5»)."""
        d = Path(tempfile.mkdtemp())
        for x in ("v", "m", "h", "p"):
            (d / x).mkdir()
        (d / "fails").write_text(str(fails))
        script = (f'S="{d}"; L="{d}"; B="{d}/b"; K=0\n'
                  'spec() { return 0; }\n'
                  'rver() { echo aaaa1111bbbb; }\n'
                  'rd() { cat "$1" 2>/dev/null; }\n'
                  'u() { "$@"; }\n'
                  'man() { echo m; }\n'
                  'sleep() { :; }\n'
                  'log() { echo "LOG $*"; }\n'
                  'st() { echo "ST $1 $2"; }\n'
                  'flock() { :; }\n'
                  f'get() {{ local c; c=$(cat "{d}/fails"); echo GET; [ "$c" -gt 0 ] && {{ echo $((c - 1)) > "{d}/fails"; return 1; }}; return 0; }}\n'
                  + self.fn("unpack") + "\nunpack g\n")
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        return r.stdout, (d / "v" / "g").read_text().strip() if (d / "v" / "g").exists() else None

    def test_unpack_retries(self):
        out, v = self.unpack_run(2)                       # дважды испортилось — третья попытка удалась
        self.assertEqual(out.count("GET"), 3)
        self.assertIn("ST g ok", out)
        self.assertEqual(v, "aaaa1111bbbb")
        out, v = self.unpack_run(5)                       # всегда портится — после трёх попыток ошибка, версия не записана
        self.assertEqual(out.count("GET"), 3)
        self.assertIn("ST g err", out)
        self.assertIsNone(v)

    def test_versions(self):
        single = [("a.tar.zst", "11112222aaaa", "5")]
        chunked = [("a.tar.zst.parts", "33334444bbbb", "1"), ("a.tar.zst.gabcdef01-0000", "x", "9")]
        self.assertEqual(self.sh(single, "rver a"), "11112222aaaa")
        self.assertEqual(self.sh(chunked, "rver a"), "33334444bbbb")
        self.assertEqual(self.sh(single + chunked, "rver a"), "33334444bbbb")       # при обоих — оглавление
        self.assertEqual(self.sh(chunked + [("a.tar.zst.33334444", "dd", "1")], "rver a"), "33334444bbbb+dd")
        self.assertEqual(self.sh(chunked + [("a.tar.zst.33334444.parts", "ee", "1")], "rver a"), "33334444bbbb+ee")
        self.assertEqual(self.sh([], "rver a"), "")

    def version_check(self, stale, fresh, known):
        """Кусок pack от «rv=$(rver…» до ошибки «другая версия»: stale — список в начале прохода ($LS),
        fresh — что вернёт перечитанный список (rl), known — версия, которую знает машина ($S/v)."""
        body = re.search(r'^pack\(\) \{\n.*?\n\}$', WOLF, re.S | re.M).group(0)
        part = re.search(r'^(  rv=\$\(rver "\$n"\); lv=.*?другая версия.*?\n  fi\n)', body, re.S | re.M).group(1)
        d = Path(tempfile.mkdtemp())
        (d / "v").mkdir()
        (d / "v" / "game--X").write_text(known + "\n")
        (d / "w").mkdir()
        rows = lambda r: "".join("\t".join(x) + "\n" for x in r)
        (d / "ls").write_text(rows(stale))
        (d / "fresh").write_text(rows(fresh))
        script = (f'LS="{d}/ls"; S="{d}"; w="{d}/w"; R=облако\n'
                  'rd() { cat "$1" 2>/dev/null; }\n'
                  'st() { echo "ST $1 $2"; }\n'
                  'gamearch() { return 0; }\n'
                  'drop() { echo DROP; }\n'
                  'rclone() { return 1; }\n'
                  f'rl() {{ : > "{d}/rl-called"; cat "{d}/fresh"; }}\n'   # stderr pack глушит — метка файлом
                  f'{self.fn("rver")}\n'
                  f'check() {{ local n=game--X rv lv\n{part}  echo "ДАЛЬШЕ $rv"; }}\ncheck\n')
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        return r.stdout.strip(), (d / "rl-called").exists()

    BASE = ("game--X.tar.zst.parts", "11112222aaaa", "1")

    def test_parallel_push_is_not_another_instance(self):
        # список снят до того, как параллельный проход выгрузил новую дельту; машина уже знает новую версию
        out, reread = self.version_check(stale=[self.BASE, ("game--X.tar.zst.11112222", "old", "1")],
                                         fresh=[self.BASE, ("game--X.tar.zst.11112222", "new", "1")],
                                         known="11112222aaaa+new")
        self.assertEqual(out, "ДАЛЬШЕ 11112222aaaa+new")                  # без ошибки, дальше — с верной версией
        self.assertTrue(reread)

    def test_really_other_version_is_still_an_error(self):
        out, _ = self.version_check(stale=[self.BASE, ("game--X.tar.zst.11112222", "other", "1")],
                                    fresh=[self.BASE, ("game--X.tar.zst.11112222", "other", "1")],
                                    known="11112222aaaa+mine")
        self.assertEqual(out, "ST game--X err")

    def test_same_version_does_not_reread_cloud(self):
        out, reread = self.version_check(stale=[self.BASE], fresh=[], known="11112222aaaa")
        self.assertEqual(out, "ДАЛЬШЕ 11112222aaaa")
        self.assertFalse(reread)

    def test_names(self):
        rows = [("sgame--X.tar.zst.parts", "h", "1"), ("sgame--X.tar.zst.g0123abcd-0000", "h", "1"),
                ("sgame--Y.tar.zst", "h", "1"), ("sgame--Y.tar.zst.11112222", "h", "1"),
                ("sgame--Z.tar.zst", "h", "1"), ("sgame--Z.tar.zst.parts", "h", "1"), ("identity.tar.zst", "h", "1")]
        self.assertEqual(self.sh(rows, "names '^sgame--'").split("\n"), ["sgame--X", "sgame--Y", "sgame--Z"])



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


class TestMacTrash(unittest.TestCase):
    """«Убрать» игру без машины (drive.trash): большая игра — сотня частей, поэтому параллельно; оглавление — первым,
    не удалилось — части не трогаем (архив остаётся целым и читается)."""

    def trash(self, files, fail=()):
        from unittest import mock
        sys.path.insert(0, str(ROOT))
        import drive
        calls, lock = [], threading.Lock()

        def fake(*a, **k):
            name = a[-1].rsplit("/", 1)[-1]
            with lock:
                calls.append(name)
            return subprocess.CompletedProcess([], 1 if name in fail else 0, stdout="", stderr="")
        with mock.patch.object(drive, "_env", lambda *a, **k: {}), mock.patch.object(drive, "rclone", fake):
            return drive.trash(files), calls

    FILES = ["X.tar.zst.g0123abcd-0000", "X.tar.zst.g0123abcd-0001", "X.tar.zst.parts", "X.tar.zst.1a2b3c4d"]

    def test_index_first_then_everything(self):
        ok, calls = self.trash(self.FILES)
        self.assertTrue(ok)
        self.assertEqual(calls[0], "X.tar.zst.parts")
        self.assertEqual(sorted(calls), sorted(self.FILES))

    def test_index_not_removed_parts_kept(self):
        ok, calls = self.trash(self.FILES, fail={"X.tar.zst.parts"})
        self.assertFalse(ok)
        self.assertEqual(calls, ["X.tar.zst.parts"])


class TestDriveAbuseFlag(unittest.TestCase):
    """[v5.14] Помеченный Google файл скачивается: флаг есть и у обычного rclone, и у частей."""

    def test_flag_on_both_download_paths(self):
        self.assertEqual(WOLF.count("--drive-pacer-burst=200 --drive-acknowledge-abuse"), 2)
        self.assertIn('"--drive-acknowledge-abuse"', CODE)


if __name__ == "__main__":
    unittest.main()

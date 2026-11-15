# Сбой упаковщика при выгрузке не подменяет уже лежащий в облаке архив.
# tar|zstd пишут только подготовку. Боевыми одиночный файл, .parts и дельта становятся
# после успешного конвейера и повторной проверки. finish при обрыве сообщает об ошибке,
# повторная попытка публикует новую версию.

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from test_game_select import WOLF
from test_parts import CODE, RCLONE, ZSTD


def func(name):
    """Функция wolf. Сначала строка целиком: иначе «{ ... }» на одной строке захватит чужой код до следующего «}»."""
    found = (re.search(rf"^{name}\(\) *\{{[^\n]*\}}$", WOLF, re.M)
             or re.search(rf"^{name}\(\) *\{{.*?\n\}}$", WOLF, re.S | re.M))
    assert found, name
    return found.group(0)

GNUBIN = [p for p in (
    "/opt/homebrew/opt/findutils/libexec/gnubin",
    "/opt/homebrew/opt/coreutils/libexec/gnubin",
    "/opt/homebrew/opt/gnu-tar/libexec/gnubin",
    "/usr/local/opt/findutils/libexec/gnubin",
    "/usr/local/opt/coreutils/libexec/gnubin",
    "/usr/local/opt/gnu-tar/libexec/gnubin",
) if os.path.isdir(p)]
TOOLS = RCLONE and ZSTD and len(GNUBIN) >= 3


def hashes(root: Path):
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def cloud_hashes(cloud: Path):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(cloud.iterdir()) if path.is_file()}


@unittest.skipUnless(TOOLS, "нужны rclone, zstd и GNU find/tar/coreutils")
class PackPublish(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cloud = self.root / "cloud"
        self.home = self.root / "home"
        self.game = self.home / "sa/steamapps/common/Game"
        self.state = self.root / "S"
        self.tmp = self.root / "T"
        self.lock = self.root / "L"
        for path in (self.cloud, self.game, self.state, self.tmp, self.lock):
            path.mkdir(parents=True)
        self.parts = self.root / "wolf-parts.py"
        self.parts.write_text(CODE)
        broken = self.root / "broken"
        broken.mkdir()
        wrapper = broken / "zstd"
        wrapper.write_text(
            "#!/bin/bash\n"
            'if [[ " $* " == *" -d "* || " $* " == *" -dcq "* ]]; then exec "$REAL_ZSTD" "$@"; fi\n'
            "dd bs=64 count=1 status=none 2>/dev/null\n"
            "cat >/dev/null\n"
            "exit 1\n")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        self.broken = broken
        self.script = self.root / "wolf"
        names = ("st", "rd", "spec", "F", "ls_", "man", "sz", "delsz", "gamearch", "rver", "rl", "idhint", "stable",
                 "pack", "get", "del", "xt_user", "unpack", "finish_run")
        body = "\n".join(func(name) for name in names).replace("/usr/local/bin/wolf-parts.py", str(self.parts))
        self.script.write_text(
            "#!/bin/bash\n"
            f'S="{self.state}"; T="{self.tmp}"; L="{self.lock}"; R="{self.cloud}"\n'
            'DH="' + str(self.home) + '"\n'
            "SR=sa; ZL=3; ZG=1; LS=$T/ls\n"
            "mkdir -p \"$S\"/{h,m,v,p,st,nt,x,pg} \"$T\" \"$L\"\n"
            + body + "\n"
            + r'''
u() { "$@"; }
flock() { return 0; }
log() { printf '%s\n' "$*"; }
sr() { SR=sa; }
rc() { rclone "$@" --retries=1 --low-level-retries=2; }
rcp() { rc "$@"; }
parts() {
  local c=$1 rc
  shift
  WOLF_TMP="$T" python3 "''' + str(self.parts) + r'''" "$c" "$R" "$@"
  rc=$?
  if [ "$c" = publish ] && [ "$rc" -eq 0 ] && [ -n "${WOLF_DIE_AFTER_PUBLISH:-}" ]; then exit 99; fi
  return "$rc"
}
case ${1:-} in
  finish-run) finish_run ;;
  shutdown) export NOW=1; pack sgame--Game || exit 1 ;;
  pack) export NOW=1; pack sgame--Game ;;
  steam-state) spec steam-state || exit 1; man steam-state >/dev/null ;;
  unpack)
    rm -f "$S/v/sgame--Game" "$S/h/sgame--Game" "$S/m/sgame--Game" "$S/id/sgame--Game"
    rl > "$LS" || exit 1
    unpack sgame--Game
    ;;
  cache)
    mkdir -p "$DH/$SR/appcache/httpcache" "$DH/$SR/appcache/keep"
    printf a > "$DH/$SR/appcache/keep/x"
    printf b > "$DH/$SR/appcache/httpcache/no"
    export NOW=1
    pack steam-cache || exit 1
    rm -rf "$DH/$SR/appcache" "$S/h/steam-cache" "$S/m/steam-cache" "$S/v/steam-cache"
    rl > "$LS" || exit 1
    unpack steam-cache || exit 1
    [ ! -e "$S/m/steam-cache" ]
    [ -s "$S/h/steam-cache" ]
    man steam-cache | sha1sum | cut -c1-40 > "$T/fp"
    cmp -s "$S/h/steam-cache" "$T/fp"
    ;;
esac
''')
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)

    def env(self, broken=False, part_bytes=None, rclone_first=None, stub=None, extra=None):
        path = list(GNUBIN)
        if broken:
            path.insert(0, str(self.broken))
        if rclone_first:
            path.insert(0, str(rclone_first))
        if stub:
            path.insert(0, str(stub))
        if ZSTD:
            path.append(str(Path(ZSTD).parent))
        if RCLONE:
            path.append(str(Path(RCLONE).parent))
        env = os.environ.copy()
        env.update({
            "PATH": ":".join(path + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
            "RCLONE_CONFIG": "/nonexistent",
            "REAL_ZSTD": ZSTD or "",
            "LC_ALL": "C",
            "HOME": str(self.root),
        })
        if part_bytes:
            env["WOLF_PART_BYTES"] = str(part_bytes)
        if extra:
            env.update(extra)
        return env

    def wolf(self, *args, broken=False, part_bytes=None, rclone_first=None, stub=None, extra=None):
        return subprocess.run([str(self.script), *args], capture_output=True, text=True,
                              env=self.env(broken, part_bytes, rclone_first, stub, extra))

    def write_game(self, note, big=None):
        if self.game.exists():
            shutil.rmtree(self.game)
        self.game.mkdir(parents=True)
        (self.game / "note.txt").write_text(note)
        if big is not None:
            (self.game / "big.bin").write_bytes(big)

    def finish_broken(self, part_bytes=None):
        before = cloud_hashes(self.cloud)
        run = self.wolf("finish-run", broken=True, part_bytes=part_bytes)
        finish = (self.state / "st/finish").read_text()
        game = (self.state / "st/sgame--Game").read_text()
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr + finish)
        self.assertIn("|err|", finish)
        self.assertNotIn("|ok|", finish)
        self.assertIn("НЕ удаляю", finish)
        self.assertIn("ошибка выгрузки", game)
        self.assertEqual(cloud_hashes(self.cloud), before)
        return before

    def restored(self, part_bytes=None):
        if self.game.exists():
            shutil.rmtree(self.game)
        run = self.wolf("unpack", part_bytes=part_bytes)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        status = (self.state / "st/sgame--Game").read_text()
        self.assertIn("|ok|", status, status + run.stdout + run.stderr)
        return hashes(self.game)

    def test_broken_zstd_keeps_single_archive(self):
        self.write_game("версия-1")
        first = hashes(self.game)
        packed = self.wolf("pack")
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        self.write_game("версия-2")
        second = hashes(self.game)
        self.finish_broken()
        self.assertEqual(self.restored(), first)
        self.write_game("версия-2")
        again = self.wolf("pack")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.restored(), second)

    def test_broken_zstd_keeps_previous_delta(self):
        big = os.urandom(256 * 1024)
        self.write_game("1", big)
        packed = self.wolf("pack")
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        (self.game / "note.txt").write_text("2")          # большой файл не переписываем: иначе это новая база, не дельта
        changed = self.wolf("pack")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        previous = hashes(self.game)
        names = cloud_hashes(self.cloud)
        self.assertTrue(any(name.startswith("sgame--Game.tar.zst.") and not name.endswith(".part")
                            for name in names), names)
        (self.game / "note.txt").write_text("3")
        self.finish_broken()
        self.assertEqual(cloud_hashes(self.cloud), names)
        self.assertEqual(self.restored(), previous)
        self.write_game("3", big)
        retry = hashes(self.game)
        again = self.wolf("pack")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.restored(), retry)

    def test_broken_zstd_keeps_previous_parts(self):
        blob = os.urandom(120_000)
        self.write_game("часть", blob)
        first = hashes(self.game)
        packed = self.wolf("pack", part_bytes=50_000)
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        self.assertTrue(any(name.endswith(".parts") for name in cloud_hashes(self.cloud)))
        blob2 = bytearray(blob)
        blob2[-1] ^= 0xff
        self.write_game("часть", bytes(blob2))
        second = hashes(self.game)
        self.finish_broken(part_bytes=50_000)
        self.assertEqual(self.restored(part_bytes=50_000), first)
        self.write_game("часть", bytes(blob2))
        again = self.wolf("pack", part_bytes=50_000)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.restored(part_bytes=50_000), second)

    def lost_rclone(self, happened):
        """moveto оглавления отвечает ошибкой. happened — файл всё же переименован. Дальше md5sum тоже падает."""
        bin_dir = self.root / ("lost-yes" if happened else "lost-no")
        bin_dir.mkdir(exist_ok=True)
        mark = self.root / ("mark-yes" if happened else "mark-no")
        mark.unlink(missing_ok=True)
        do = f'"{RCLONE}" "$@"' if happened else ":"
        wrap = bin_dir / "rclone"
        wrap.write_text(
            "#!/bin/bash\n"
            f'mark="{mark}"\n'
            'if [[ " $* " == *" moveto "* && " $* " == *".parts.new"* ]]; then\n'
            f'  {do}\n'
            '  echo 1 > "$mark"\n'
            "  exit 1\n"
            "fi\n"
            'if [[ " $* " == *" md5sum "* && -f "$mark" ]]; then exit 1; fi\n'
            f'exec "{RCLONE}" "$@"\n')
        wrap.chmod(wrap.stat().st_mode | stat.S_IEXEC)
        return bin_dir

    def test_lost_parts_reply_keeps_machine_and_restores_both_outcomes(self):
        blob = os.urandom(120_000)
        self.write_game("было", blob)
        packed = self.wolf("pack", part_bytes=50_000)
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        old = hashes(self.game)
        changed = bytearray(blob)
        changed[-1] ^= 0xff
        self.write_game("стало", bytes(changed))
        new = hashes(self.game)
        for happened, restored_now in ((True, new), (False, old)):
            with self.subTest(happened=happened):
                if happened:
                    self.write_game("стало", bytes(changed))
                else:
                    self.write_game("было", blob)
                    self.wolf("pack", part_bytes=50_000)
                    self.write_game("стало", bytes(changed))
                run = self.wolf("finish-run", part_bytes=50_000, rclone_first=self.lost_rclone(happened))
                finish = (self.state / "st/finish").read_text()
                game = (self.state / "st/sgame--Game").read_text()
                self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr + finish)
                self.assertIn("не подтверждена", finish)
                self.assertIn("НЕ удаляю", finish)
                self.assertIn("не подтверждена", game)
                self.assertTrue((self.state / "pending/sgame--Game").is_file())
                self.assertTrue(any(self.tmp.glob("attempt-*")))
                self.assertEqual(self.restored(part_bytes=50_000), restored_now)
                self.write_game("стало", bytes(changed))
                again = self.wolf("pack", part_bytes=50_000)
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                self.assertFalse((self.state / "pending/sgame--Game").exists())
                self.assertEqual(self.restored(part_bytes=50_000), new)

    def fail_index_probe(self, code):
        bin_dir = self.root / f"probe-{code}"
        bin_dir.mkdir(exist_ok=True)
        wrap = bin_dir / "rclone"
        wrap.write_text(
            "#!/bin/bash\n"
            'if [[ " $* " == *" moveto "* && " $* " == *".parts.new"* ]]; then exit 1; fi\n'
            'for a in "$@"; do\n'
            '  case "$a" in\n'
            '    *.parts.new) ;;\n'
            f'    *.parts) [[ " $* " == *" md5sum "* ]] && exit {int(code)} ;;\n'
            '  esac\ndone\n'
            f'exec "{RCLONE}" "$@"\n')
        wrap.chmod(wrap.stat().st_mode | stat.S_IEXEC)
        return bin_dir

    def test_new_save_is_packed_in_the_same_finish_after_lost_moveto(self):
        blob = os.urandom(120_000)
        second = bytearray(blob)
        second[0] ^= 0x11
        third = bytearray(blob)
        third[0] ^= 0x22
        for happened in (True, False):
            with self.subTest(happened=happened):
                for path in self.cloud.iterdir():
                    path.unlink()
                pending = self.state / "pending"
                if pending.exists():
                    shutil.rmtree(pending)
                self.write_game("первый", blob)
                self.assertEqual(self.wolf("pack", part_bytes=50_000).returncode, 0)
                self.write_game("второй", bytes(second))
                lost = self.wolf("finish-run", part_bytes=50_000, rclone_first=self.lost_rclone(happened))
                lost_status = (self.state / "st/finish").read_text()
                self.assertNotEqual(lost.returncode, 0, lost.stdout + lost.stderr + lost_status)
                self.assertNotIn("|ok|", lost_status)
                self.write_game("третий", bytes(third))
                latest = hashes(self.game)
                done = self.wolf("finish-run", part_bytes=50_000)
                done_status = (self.state / "st/finish").read_text()
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr + done_status)
                self.assertIn("|ok|", done_status)
                self.assertEqual(self.restored(part_bytes=50_000), latest)

    def test_single_archive_survives_until_parts_publish_is_confirmed(self):
        self.write_game("маленький")
        self.assertEqual(self.wolf("pack").returncode, 0, )
        single = hashes(self.game)
        single_bytes = (self.cloud / "sgame--Game.tar.zst").read_bytes()
        self.assertFalse(any(name.endswith(".parts") for name in cloud_hashes(self.cloud)))
        blob = os.urandom(120_000)
        self.write_game("большой", blob)
        grown = hashes(self.game)
        lost = self.wolf("finish-run", part_bytes=50_000, rclone_first=self.fail_index_probe(1))
        lost_status = (self.state / "st/finish").read_text()
        self.assertNotEqual(lost.returncode, 0, lost.stdout + lost.stderr + lost_status)
        self.assertNotIn("|ok|", lost_status)
        self.assertIn("не подтверждена", lost_status)
        self.assertIn("не проверена", (self.state / "st/sgame--Game").read_text())
        self.assertTrue((self.cloud / "sgame--Game.tar.zst").is_file())
        self.assertTrue(any(name.endswith(".parts.new") for name in cloud_hashes(self.cloud)))
        still = self.wolf("pack", part_bytes=50_000, rclone_first=self.fail_index_probe(1))
        self.assertNotEqual(still.returncode, 0, still.stdout + still.stderr)
        self.assertEqual((self.cloud / "sgame--Game.tar.zst").read_bytes(), single_bytes)
        self.assertEqual(self.restored(), single)
        self.write_game("большой", blob)
        done = self.wolf("finish-run", part_bytes=50_000)
        done_status = (self.state / "st/finish").read_text()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr + done_status)
        self.assertIn("|ok|", done_status)
        self.assertEqual(self.restored(part_bytes=50_000), grown)

    def assert_finish_err(self, run):
        status = (self.state / "st/finish").read_text()
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr + status)
        self.assertNotIn("|ok|", status)
        self.assertIn("НЕ удаляю", status)
        return status

    def assert_finish_ok(self, run):
        status = (self.state / "st/finish").read_text()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr + status)
        self.assertIn("|ok|", status)

    def test_mkdir_and_vanished_file_keep_the_previous_archive(self):
        self.write_game("было")
        (self.game / "extra.txt").write_text("старое")
        first = hashes(self.game)
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("стало")
        (self.game / "extra.txt").write_text("новое")
        second = hashes(self.game)
        no_space = self.root / "no-space"
        no_space.mkdir()
        mkdir = no_space / "mkdir"
        mkdir.write_text('#!/bin/bash\necho "mkdir: No space left on device" >&2\nexit 1\n')
        mkdir.chmod(mkdir.stat().st_mode | stat.S_IEXEC)
        lost = self.wolf("finish-run", stub=no_space)
        self.assert_finish_err(lost)
        self.assertIn("нет места", (self.state / "st/sgame--Game").read_text())
        self.assertEqual(self.restored(), first)
        self.write_game("стало")
        (self.game / "extra.txt").write_text("новое")
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), second)

        (self.game / "note.txt").write_text("ещё")
        third = hashes(self.game)
        hid = self.root / "hide-tar"
        hid.mkdir()
        tar = hid / "tar"
        real_tar = next(path for path in GNUBIN if (Path(path) / "tar").exists())
        tar.write_text(
            "#!/bin/bash\n"
            'if [ -n "${WOLF_HIDE:-}" ] && [ -e "$WOLF_HIDE" ]; then\n'
            '  mv "$WOLF_HIDE" "$WOLF_HIDE.away"\n'
            f'  "{real_tar}/tar" "$@"\n'
            '  code=$?\n'
            '  mv "$WOLF_HIDE.away" "$WOLF_HIDE"\n'
            '  exit "$code"\n'
            "fi\n"
            f'exec "{real_tar}/tar" "$@"\n')
        tar.chmod(tar.stat().st_mode | stat.S_IEXEC)
        hidden = self.wolf("finish-run", stub=hid, extra={"WOLF_HIDE": str(self.game / "extra.txt")})
        self.assert_finish_err(hidden)
        self.assertIn("ошибка выгрузки", (self.state / "st/sgame--Game").read_text())
        self.assertTrue((self.game / "extra.txt").is_file())
        self.assertEqual(self.restored(), second)
        self.write_game("ещё")
        (self.game / "extra.txt").write_text("новое")
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), third)

    def test_delete_before_manifest_is_stored(self):
        self.write_game("остаётся")
        (self.game / "extra.txt").write_text("уйдёт")
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "extra.txt").unlink()
        kept = hashes(self.game)
        done = self.wolf("finish-run")
        done_status = (self.state / "st/finish").read_text()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr + done_status)
        self.assertIn("|ok|", done_status)
        self.assertEqual(self.restored(), kept)
        self.assertNotIn("extra.txt", kept)

    def wipe_cloud(self):
        for path in self.cloud.iterdir():
            path.unlink()
        pending = self.state / "pending"
        if pending.exists():
            shutil.rmtree(pending)
        for sub in ("v", "h", "m", "id", "st", "p"):
            folder = self.state / sub
            if folder.exists():
                for path in folder.iterdir():
                    path.unlink()

    def test_crash_after_publish_then_saves_the_newer_save(self):
        blob = os.urandom(120_000)
        for parts in (False, True):
            with self.subTest(parts=parts):
                self.wipe_cloud()
                kw = {"part_bytes": 50_000} if parts else {}
                first_big = blob if parts else None
                self.write_game("первый", first_big)
                self.assertEqual(self.wolf("pack", **kw).returncode, 0)
                names = cloud_hashes(self.cloud)
                if parts:
                    self.assertTrue(any(name.endswith(".parts") for name in names), names)
                else:
                    self.assertIn("sgame--Game.tar.zst", names)
                    self.assertFalse(any(name.endswith(".parts") for name in names), names)
                nxt = bytearray(blob)
                nxt[0] ^= 0x10
                self.write_game("второй", bytes(nxt) if parts else None)
                died = self.wolf("finish-run", extra={"WOLF_DIE_AFTER_PUBLISH": "1"}, **kw)
                self.assert_finish_err(died)
                self.assertTrue((self.state / "pending/sgame--Game").is_file())
                latest_big = bytearray(blob)
                latest_big[0] ^= 0x20
                self.write_game("третий", bytes(latest_big) if parts else None)
                latest = hashes(self.game)
                done = self.wolf("finish-run", **kw)
                self.assert_finish_ok(done)
                self.assertFalse((self.state / "pending/sgame--Game").exists())
                self.assertEqual(self.restored(**kw), latest)

    def test_crash_during_stage_reclaims_only_its_generation_on_retry(self):
        self.write_game("original")
        self.assertEqual(self.wolf("pack").returncode, 0)
        original = hashes(self.game)
        big = self.game / "big.bin"
        big.write_bytes(os.urandom(120_000))
        foreign = self.cloud / "sgame--Game.tar.zst.gdeadbeef-0000"
        foreign.write_bytes(b"unrelated generation")
        before = cloud_hashes(self.cloud)

        tools = self.root / "interrupted-stage"
        tools.mkdir()
        wrapper = tools / "rclone"
        wrapper.write_text(
            '#!/bin/bash\n'
            f'"{RCLONE}" "$@" || exit $?\n'
            'if [[ " $* " == *" copyto "* && " $* " =~ \\.g[0-9a-f]{8}-0000 ]]; then\n'
            '  [[ -e /dev/fd/8 ]] || exit 1\n'
            '  kill -KILL "$PPID"\n'
            'fi\n'
        )
        wrapper.chmod(0o755)
        kw = {"part_bytes": 50_000, "extra": {"WOLF_PART_PAR": "1"}}
        crashed = self.wolf("finish-run", rclone_first=tools, **kw)
        self.assert_finish_err(crashed)
        journal = self.state / "staging/sgame--Game"
        self.assertTrue(journal.exists(), crashed.stdout + crashed.stderr)
        _, obj, gen = json.loads(journal.read_text())
        self.assertTrue((self.cloud / f"{obj}.g{gen}-0000").is_file())
        self.assertFalse((self.state / "pending/sgame--Game").exists())
        interrupted = cloud_hashes(self.cloud)
        self.assertEqual({name: interrupted[name] for name in before}, before)

        # Отменённая правка обычно завершила бы pack без обращения к подготовке.
        big.unlink()
        self.assertEqual(hashes(self.game), original)
        wrapper.write_text(
            '#!/bin/bash\n'
            'if [[ " $* " == *" cat "* && " ${*: -1}" == *".parts" ]]; then exit 1; fi\n'
            f'exec "{RCLONE}" "$@"\n'
        )
        self.assert_finish_err(self.wolf("finish-run", rclone_first=tools, **kw))
        self.assertTrue(journal.exists())
        self.assertEqual(cloud_hashes(self.cloud), interrupted)
        retry = self.wolf("pack", **kw)
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertFalse(journal.exists())
        self.assertEqual(cloud_hashes(self.cloud), before)
        self.assertEqual(self.restored(part_bytes=kw["part_bytes"]), original)

    def test_foreign_upload_is_still_a_version_conflict(self):
        self.wipe_cloud()
        self.write_game("первый")
        self.assertEqual(self.wolf("pack").returncode, 0)
        self.write_game("второй")
        died = self.wolf("finish-run", extra={"WOLF_DIE_AFTER_PUBLISH": "1"})
        self.assert_finish_err(died)
        published = self.cloud / "sgame--Game.tar.zst"
        published.write_bytes(published.read_bytes() + b"foreign")
        self.write_game("третий")
        run = self.wolf("finish-run")
        self.assert_finish_err(run)
        self.assertIn("другая версия", (self.state / "st/sgame--Game").read_text())

    def test_new_slot_after_tar_is_uploaded_on_retry(self):
        self.write_game("было")
        (self.game / "extra.txt").write_text("старое")
        first = hashes(self.game)
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("стало")
        slot = self.game / "slot2.sav"
        stub = self.tar_stub("tar-new",
            f'"{self.gnu_tar()}" "$@"\n'
            'code=$?\n'
            'if [ -n "${WOLF_NEW:-}" ]; then printf slot > "$WOLF_NEW"; fi\n'
            'exit "$code"\n')
        lost = self.wolf("finish-run", stub=stub, extra={"WOLF_NEW": str(slot)})
        self.assert_finish_err(lost)
        self.assertIn("после чтения", (self.state / "st/sgame--Game").read_text())
        self.assertEqual(self.restored(), first)
        self.write_game("стало")
        (self.game / "extra.txt").write_text("старое")
        (self.game / "slot2.sav").write_bytes(b"slot")
        current = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))
        got = self.restored()
        self.assertEqual(got, current)
        self.assertEqual(got["slot2.sav"], hashlib.sha256(b"slot").hexdigest())

    def find_stub(self):
        real = next(Path(base) / "find" for base in GNUBIN if (Path(base) / "find").exists())
        folder = self.root / "find-skip"
        folder.mkdir()
        script = folder / "find"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            f"real = {str(real)!r}\n"
            "proc = subprocess.run([real, *sys.argv[1:]], stdout=subprocess.PIPE)\n"
            "data = proc.stdout\n"
            "if os.environ.get('WOLF_SKIP_SAVE'):\n"
            "    kept = []\n"
            "    for rec in data.split(b'\\0'):\n"
            "        if rec.split(b'\\t', 1)[0].endswith(b'slot.sav'):\n"
            "            continue\n"
            "        kept.append(rec)\n"
            "    sys.stdout.buffer.write(b'\\0'.join(kept))\n"
            "    sys.stderr.write(\"find: 'slot.sav': Permission denied\\n\")\n"
            "    raise SystemExit(1)\n"
            "sys.stdout.buffer.write(data)\n"
            "raise SystemExit(proc.returncode)\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return folder

    def test_skipped_save_in_both_walks_keeps_the_previous_archive(self):
        self.write_game("было")
        (self.game / "slot.sav").write_bytes(b"save-1")
        first = hashes(self.game)
        packed = self.wolf("pack")
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        (self.game / "note.txt").write_text("стало")
        (self.game / "slot.sav").write_bytes(b"save-2")
        lost = self.wolf("finish-run", stub=self.find_stub(), extra={"WOLF_SKIP_SAVE": "1"})
        self.assert_finish_err(lost)
        self.assertIn("обход", (self.state / "st/sgame--Game").read_text())
        self.assertEqual(self.restored(), first)
        self.write_game("стало")
        (self.game / "slot.sav").write_bytes(b"save-2")
        current = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))
        got = self.restored()
        self.assertEqual(got, current)
        self.assertEqual(got["slot.sav"], hashlib.sha256(b"save-2").hexdigest())

    def test_partial_control_walk_keeps_the_previous_archive(self):
        self.write_game("было")
        (self.game / "slot.sav").write_bytes(b"save-1")
        first = hashes(self.game)
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("стало")
        real = next(Path(base) / "find" for base in GNUBIN if (Path(base) / "find").exists())
        count = self.root / "find-n"
        folder = self.root / "find-control"
        folder.mkdir()
        script = folder / "find"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, subprocess, sys\n"
            f"real = {str(real)!r}\n"
            f"count = pathlib.Path({str(count)!r})\n"
            "n = int(count.read_text()) + 1 if count.exists() else 1\n"
            "count.write_text(str(n))\n"
            "proc = subprocess.run([real, *sys.argv[1:]], stdout=subprocess.PIPE)\n"
            "if n == 1:\n"
            "    sys.stdout.buffer.write(proc.stdout)\n"
            "    raise SystemExit(proc.returncode)\n"
            "kept = []\n"
            "for rec in proc.stdout.split(b'\\0'):\n"
            "    if rec.split(b'\\t', 1)[0].endswith(b'slot.sav'):\n"
            "        continue\n"
            "    kept.append(rec)\n"
            "sys.stdout.buffer.write(b'\\0'.join(kept))\n"
            "sys.stderr.write(\"find: 'slot.sav': Permission denied\\n\")\n"
            "raise SystemExit(1)\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        lost = self.wolf("finish-run", stub=folder)
        self.assert_finish_err(lost)
        self.assertIn("обход", (self.state / "st/sgame--Game").read_text())
        self.assertGreaterEqual(int(count.read_text()), 2)
        self.assertEqual(self.restored(), first)
        self.write_game("стало")
        (self.game / "slot.sav").write_bytes(b"save-1")
        current = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))
        got = self.restored()
        self.assertEqual(got, current)
        self.assertEqual(got["slot.sav"], hashlib.sha256(b"save-1").hexdigest())

    def test_sort_failure_after_a_full_walk_keeps_the_previous_archive(self):
        self.write_game("было")
        (self.game / "extra.txt").write_text("старое")
        first = hashes(self.game)
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("стало")
        folder = self.root / "sort-fail"
        folder.mkdir()
        script = folder / "sort"
        script.write_text("#!/bin/bash\ncat >/dev/null\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        lost = self.wolf("finish-run", stub=folder)
        self.assert_finish_err(lost)
        self.assertIn("обход", (self.state / "st/sgame--Game").read_text())
        self.assertEqual(self.restored(), first)
        self.write_game("стало")
        (self.game / "extra.txt").write_text("старое")
        current = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), current)

    def counting_find(self, count: Path):
        real = next(Path(base) / "find" for base in GNUBIN if (Path(base) / "find").exists())
        folder = self.root / "find-count"
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
        script = folder / "find"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            f"real = {str(real)!r}\n"
            f"count = pathlib.Path({str(count)!r})\n"
            "n = int(count.read_text()) + 1 if count.exists() else 1\n"
            "count.write_text(str(n))\n"
            "os.execv(real, [real, *sys.argv[1:]])\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return folder

    def reads(self):
        text = (self.tmp / "content-read").read_text().strip().split()
        return int(text[0]), int(text[1])

    def restore_mtime(self, path, payload):
        self.assertEqual(path.stat().st_size, len(payload))
        stamp = path.stat().st_mtime_ns
        path.write_bytes(payload)
        os.utime(path, ns=(stamp, stamp))
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(path.stat().st_size, len(payload))

    def test_same_mtime_rewrite_is_restored(self):
        self.write_game("база")
        (self.game / "slot.sav").write_bytes(b"save-1")
        (self.game / "big.bin").write_bytes(b"x" * 200000)
        self.assertEqual(self.wolf("pack").returncode, 0)
        cold = self.reads()
        self.assertGreater(cold[0], 1)
        again = self.wolf("pack")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.reads(), (0, 0))
        self.restore_mtime(self.game / "slot.sav", b"save-2")
        base = cloud_hashes(self.cloud)
        changed = self.wolf("pack")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertEqual(self.reads(), (1, len(b"save-2")))
        now = cloud_hashes(self.cloud)
        self.assertEqual(now["sgame--Game.tar.zst"], base["sgame--Game.tar.zst"])
        self.assertTrue(any(name.startswith("sgame--Game.tar.zst.") and not name.endswith(".parts")
                            for name in now))
        self.assertEqual(self.restored()["slot.sav"], hashlib.sha256(b"save-2").hexdigest())

    def test_same_mtime_rewrite_survives_a_cumulative_delta(self):
        self.write_game("база")
        (self.game / "slot.sav").write_bytes(b"save-1")
        (self.game / "big.bin").write_bytes(b"y" * 200000)
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("дельта")
        self.assertEqual(self.wolf("pack").returncode, 0)
        base = (self.cloud / "sgame--Game.tar.zst").read_bytes()
        self.restore_mtime(self.game / "slot.sav", b"save-2")
        self.assertEqual(self.wolf("finish-run").returncode, 0)
        self.assertEqual((self.cloud / "sgame--Game.tar.zst").read_bytes(), base)
        got = self.restored()
        self.assertEqual(got["slot.sav"], hashlib.sha256(b"save-2").hexdigest())
        self.assertEqual(got["note.txt"], hashlib.sha256("дельта".encode()).hexdigest())
        self.assertEqual(got["big.bin"], hashlib.sha256(b"y" * 200000).hexdigest())

    def test_next_delta_keeps_the_same_mtime_save(self):
        self.write_game("база")
        (self.game / "slot.sav").write_bytes(b"save-1")
        (self.game / "big.bin").write_bytes(b"x" * 200000)
        self.assertEqual(self.wolf("pack").returncode, 0)
        self.restore_mtime(self.game / "slot.sav", b"save-2")
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("second")
        packed = self.wolf("pack")
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        self.assertEqual(self.reads(), (1, len(b"second")))
        got = self.restored()
        self.assertEqual(got["slot.sav"], hashlib.sha256(b"save-2").hexdigest())
        self.assertEqual(got["note.txt"], hashlib.sha256(b"second").hexdigest())
        self.assertEqual(got["big.bin"], hashlib.sha256(b"x" * 200000).hexdigest())

    def test_next_delta_keeps_save_after_a_clean_restore(self):
        self.write_game("база")
        (self.game / "slot.sav").write_bytes(b"save-1")
        (self.game / "big.bin").write_bytes(b"x" * 200000)
        self.assertEqual(self.wolf("pack").returncode, 0)
        self.restore_mtime(self.game / "slot.sav", b"save-2")
        self.assertEqual(self.wolf("pack").returncode, 0)
        mid = self.restored()
        self.assertEqual(mid["slot.sav"], hashlib.sha256(b"save-2").hexdigest())
        (self.game / "note.txt").write_text("second")
        packed = self.wolf("pack")
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        got = self.restored()
        self.assertEqual(got["slot.sav"], hashlib.sha256(b"save-2").hexdigest())
        self.assertEqual(got["note.txt"], hashlib.sha256(b"second").hexdigest())
        self.assertEqual(got["big.bin"], hashlib.sha256(b"x" * 200000).hexdigest())

    def test_recreated_save_after_deletion_delta_saves_new_content(self):
        for intermediate_restore in (False, True):
            with self.subTest(intermediate_restore=intermediate_restore):
                self.wipe_cloud()
                self.write_game("база", b"x" * 200000)
                slot = self.game / "slot.sav"
                slot.write_bytes(b"save-1")
                stamp = (slot.stat().st_mtime_ns // 1_000_000_000) * 1_000_000_000
                os.utime(slot, ns=(stamp, stamp))
                self.assertEqual(self.wolf("pack").returncode, 0)
                base_bytes = (self.cloud / "sgame--Game.tar.zst").read_bytes()

                slot.unlink()
                deleted_expected = hashes(self.game)
                self.assert_finish_ok(self.wolf("finish-run"))
                self.assertEqual((self.cloud / "sgame--Game.tar.zst").read_bytes(), base_bytes)
                self.assertTrue(any(name.startswith("sgame--Game.tar.zst.") and not name.endswith(".parts")
                                    for name in cloud_hashes(self.cloud)))

                if intermediate_restore:
                    mid = self.restored()
                    self.assertEqual(mid, deleted_expected)
                    self.assertNotIn("slot.sav", mid)

                slot.write_bytes(b"save-2")
                os.utime(slot, ns=(stamp, stamp))
                self.assertEqual(slot.stat().st_mtime_ns, stamp)
                expected = hashes(self.game)
                self.assert_finish_ok(self.wolf("finish-run"))
                self.assertEqual((self.cloud / "sgame--Game.tar.zst").read_bytes(), base_bytes)

                got = self.restored()
                self.assertEqual(got, expected)
                self.assertEqual(got["slot.sav"], hashlib.sha256(b"save-2").hexdigest())

                before = cloud_hashes(self.cloud)
                quiet = self.wolf("pack")
                self.assertEqual(quiet.returncode, 0, quiet.stdout + quiet.stderr)
                self.assertEqual(self.reads(), (0, 0))
                self.assertEqual(cloud_hashes(self.cloud), before)

                (self.game / "extra.txt").write_text("после")
                expected_next = hashes(self.game)
                self.assert_finish_ok(self.wolf("finish-run"))
                self.assertEqual(self.restored(), expected_next)

    def test_recreated_save_same_as_base_after_deletion_is_restored(self):
        self.write_game("база", b"x" * 200000)
        slot = self.game / "slot.sav"
        slot.write_bytes(b"save-1")
        stamp = (slot.stat().st_mtime_ns // 1_000_000_000) * 1_000_000_000
        os.utime(slot, ns=(stamp, stamp))
        self.assertEqual(self.wolf("pack").returncode, 0)
        slot.unlink()
        self.assert_finish_ok(self.wolf("finish-run"))
        slot.write_bytes(b"save-1")
        os.utime(slot, ns=(stamp, stamp))
        expected = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), expected)

    def test_recreated_save_when_idhint_lacks_deleted_record(self):
        self.write_game("база", b"x" * 200000)
        slot = self.game / "slot.sav"
        slot.write_bytes(b"save-1")
        stamp = (slot.stat().st_mtime_ns // 1_000_000_000) * 1_000_000_000
        os.utime(slot, ns=(stamp, stamp))
        self.assertEqual(self.wolf("pack").returncode, 0)

        slot.unlink()
        self.assert_finish_ok(self.wolf("finish-run"))
        id_path = self.state / "id/sgame--Game"
        kept_rows = [rec for rec in id_path.read_bytes().split(b"\0")
                     if rec and not rec.split(b"\t", 1)[0].endswith(b"slot.sav")]
        id_path.write_bytes(b"\0".join(kept_rows) + b"\0")

        slot.write_bytes(b"save-2")
        os.utime(slot, ns=(stamp, stamp))
        expected = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), expected)

    def test_failed_finish_after_recreated_save_keeps_deletion_until_retry(self):
        self.write_game("база", b"x" * 200000)
        slot = self.game / "slot.sav"
        slot.write_bytes(b"save-1")
        stamp = (slot.stat().st_mtime_ns // 1_000_000_000) * 1_000_000_000
        os.utime(slot, ns=(stamp, stamp))
        self.assertEqual(self.wolf("pack").returncode, 0)

        slot.unlink()
        deleted_expected = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))

        slot.write_bytes(b"save-2")
        os.utime(slot, ns=(stamp, stamp))
        expected = hashes(self.game)
        self.finish_broken()
        self.assertEqual(self.restored(), deleted_expected)

        slot.write_bytes(b"save-2")
        os.utime(slot, ns=(stamp, stamp))
        self.finish_broken()
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), expected)

    def test_unpack_without_delta_reuses_the_base_manifest(self):
        self.write_game("было")
        (self.game / "slot.sav").write_bytes(b"save-1")
        first = hashes(self.game)
        self.assertEqual(self.wolf("pack").returncode, 0)
        count = self.root / "find-n"
        run = self.wolf("unpack", stub=self.counting_find(count))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(int(count.read_text()), 1)
        manifest = (self.state / "m/sgame--Game").read_bytes()
        self.assertEqual((self.state / "h/sgame--Game").read_text().strip(),
                         hashlib.sha1(manifest).hexdigest()[:40])
        self.assertEqual(hashes(self.game), first)
        before = cloud_hashes(self.cloud)
        again = self.wolf("pack")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(cloud_hashes(self.cloud), before)

    def test_k0_fingerprint_is_taken_after_restore(self):
        run = self.wolf("cache")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_missing_optional_steam_paths_are_not_a_walk_error(self):
        run = self.wolf("steam-state")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("обход", run.stderr)

    def gnu_tar(self):
        return next(path for path in GNUBIN if (Path(path) / "tar").exists()) + "/tar"

    def tar_stub(self, name, body):
        folder = self.root / name
        folder.mkdir()
        tar = folder / "tar"
        tar.write_text("#!/bin/bash\n" + body)
        tar.chmod(tar.stat().st_mode | stat.S_IEXEC)
        return folder

    def test_write_after_tar_keeps_the_previous_archive(self):
        self.write_game("было")
        (self.game / "extra.txt").write_text("старое")
        first = hashes(self.game)
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("стало")
        (self.game / "extra.txt").write_text("новое")
        second = hashes(self.game)
        real = self.gnu_tar()
        after = self.tar_stub("tar-after",
            f'"{real}" "$@"\n'
            'code=$?\n'
            'if [ -n "${WOLF_TOUCH:-}" ]; then printf x >> "$WOLF_TOUCH"; fi\n'
            'exit "$code"\n')
        lost = self.wolf("finish-run", stub=after, extra={"WOLF_TOUCH": str(self.game / "extra.txt")})
        self.assert_finish_err(lost)
        self.assertIn("после чтения", (self.state / "st/sgame--Game").read_text())
        self.assertEqual(self.restored(), first)
        self.write_game("стало")
        (self.game / "extra.txt").write_text("новое")
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), second)

    def test_tar_exit_1_keeps_the_previous_archive(self):
        self.write_game("было")
        (self.game / "extra.txt").write_text("старое")
        first = hashes(self.game)
        self.assertEqual(self.wolf("pack").returncode, 0)
        (self.game / "note.txt").write_text("ещё")
        (self.game / "extra.txt").write_text("новое")
        current = hashes(self.game)
        during = self.tar_stub("tar-exit1", f'"{self.gnu_tar()}" "$@"\nexit 1\n')
        changed = self.wolf("finish-run", stub=during)
        self.assert_finish_err(changed)
        self.assertIn("во время чтения", (self.state / "st/sgame--Game").read_text())
        self.assertEqual(self.restored(), first)
        self.write_game("ещё")
        (self.game / "extra.txt").write_text("новое")
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual(self.restored(), current)

    def test_delta_after_rebase_excludes_rebased_files(self):
        self.write_game("база", os.urandom(200_000))
        (self.game / "slot1.sav").write_bytes(b"a" * 20_000)
        (self.game / "slot2.sav").write_bytes(b"b" * 20_000)
        (self.game / "slot3.sav").write_bytes(b"c" * 40_000)
        (self.game / "next.sav").write_bytes(b"n" * 2_000)
        (self.game / "same_mtime.sav").write_bytes(b"save-1")
        self.assertEqual(self.wolf("pack").returncode, 0)
        base_v1 = (self.cloud / "sgame--Game.tar.zst").read_bytes()

        # Несколько накопительных дельт, включая правку с прежними размером и mtime.
        (self.game / "slot1.sav").write_bytes(b"A" * 20_000)
        self.restore_mtime(self.game / "same_mtime.sav", b"save-2")
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual((self.cloud / "sgame--Game.tar.zst").read_bytes(), base_v1)

        (self.game / "slot2.sav").write_bytes(b"B" * 20_000)
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual((self.cloud / "sgame--Game.tar.zst").read_bytes(), base_v1)
        self.assertTrue(any(name.startswith("sgame--Game.tar.zst.") and not name.endswith(".parts")
                            for name in cloud_hashes(self.cloud)))

        # Накопленный объём изменений >= 25% -> публикация новой полной базы (rebase).
        (self.game / "slot3.sav").write_bytes(b"C" * 40_000)
        self.assert_finish_ok(self.wolf("finish-run"))
        base_v2 = (self.cloud / "sgame--Game.tar.zst").read_bytes()
        self.assertNotEqual(base_v2, base_v1)
        self.assertFalse(any(name.startswith("sgame--Game.tar.zst.") and not name.endswith(".parts")
                             for name in cloud_hashes(self.cloud)))

        # Изменяем только один следующий файл -> новая дельта не должна включать файлы, уже вошедшие в новую базу.
        (self.game / "next.sav").write_bytes(b"N" * 2_000)
        expected = hashes(self.game)
        self.assert_finish_ok(self.wolf("finish-run"))
        self.assertEqual((self.cloud / "sgame--Game.tar.zst").read_bytes(), base_v2)

        deltas = [p for p in self.cloud.iterdir()
                  if p.is_file() and p.name.startswith("sgame--Game.tar.zst.") and not p.name.endswith(".parts")]
        self.assertEqual(len(deltas), 1, [p.name for p in self.cloud.iterdir()])
        raw_tar = subprocess.check_output([ZSTD, "-dc", str(deltas[0])])
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as tf:
            members = [m.name for m in tf.getmembers() if not m.name.startswith(".wolf-del.")]

        self.assertEqual(members, ["sa/steamapps/common/Game/next.sav"])
        self.assertEqual(self.restored(), expected)
        self.assertEqual(expected["same_mtime.sav"], hashlib.sha256(b"save-2").hexdigest())


if __name__ == "__main__":
    unittest.main()

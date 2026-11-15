# finish не должен стать ok, если при выключении список файлов Drive не прочитался.
# Иначе отсутствие списка выглядит как «изменений не было», и машину удаляют.

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_game_select import fn


def wolf(root: Path, rl: str) -> Path:
    script = root / "wolf"
    script.write_text(
        "#!/bin/bash\n"
        f'S="{root}/S"; T="{root}/T"; L="{root}/L"; R=remote\n'
        'mkdir -p "$S/st" "$T" "$L"\n'
        + fn("st") + "\n"
        + fn("finish_run") + "\n"
        + fn("bk") + "\n"
        + r'''
log() { printf '%s\n' "$*"; }
sr() { :; }
steam_off() { return 0; }
steam_gone() { :; }
shortcuts() { :; }
prog_scan() { :; }
gindex() { return 0; }
flock() { return 0; }
sleep() { echo SLEEP >> "$T/side"; }
curl() { echo CURL >> "$T/side"; }
'''
        + rl + "\n"
        + r'''
case ${1:-} in
  finish-run) finish_run ;;
  state|games|shutdown|restore|push|forget|answer|mods|lgame) bk "$@" ;;
esac
''')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


class TestFinishListing(unittest.TestCase):
    def test_unavailable_drive_list_is_not_finish_ok(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = wolf(root, "rl() { echo 'rl: список недоступен' >&2; return 1; }")
            run = subprocess.run([str(script), "finish-run"], capture_output=True, text=True)
            finish = (root / "S/st/finish").read_text()
            states = {p.name: p.read_text() for p in (root / "S/st").iterdir()}
            side = (root / "T/side").read_text() if (root / "T/side").exists() else ""
            self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr + finish)
            self.assertNotIn("|ok|", finish, states)
            self.assertIn("|err|", finish)
            self.assertIn("НЕ удаляю", finish)
            for name in ("state", "games", "mods", "lgame"):
                self.assertIn("|err|", states.get(name, ""), states)
                self.assertIn("список облака не получен", states[name])
            self.assertNotIn("CURL", side)
            self.assertNotIn("SLEEP", side)
            self.assertIn("не удалось получить список облака", run.stdout)

    def test_periodic_list_failure_does_not_write_err(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = wolf(root, "rl() { return 1; }")
            run = subprocess.run([str(script), "state"], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertFalse((root / "S/st/state").exists())
            self.assertIn("не удалось получить список облака", run.stdout)

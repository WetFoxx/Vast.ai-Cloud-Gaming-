# wolf-setup.sh v4.2: паспорта игр Steam (appmanifest_*.acf) всегда уезжают в облако со steam-state.
# Живой случай 2026-09-26: с выключенной синхронизацией игр Steam их не было в списке steam-state, выгрузка
# удаляла их из облака, и включённая потом синхронизация не вернула игру («игры нет в библиотеке Steam»).
# Запускаем настоящие функции wolf (из heredoc) в bash на поддельной папке Steam.

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WOLF = re.search(r"^w /usr/local/bin/wolf <<'WOLF'\n(.*?)^WOLF$", (ROOT / "wolf-setup.sh").read_text(),
                 re.S | re.M).group(1)


def fn(name):
    """Текст функции wolf: однострочной или многострочной (до «}» в начале строки)."""
    m = re.search(rf"^{name}\(\) *\{{.*?(?:\}}$|\n\}}$)", WOLF, re.S | re.M)
    assert m, name
    return m.group(0)


FUNCS = "\n".join([fn("F"), fn("idirs"), fn("acf_hide"), fn("acf_unhide"), fn("ls_"),
                   re.search(r"^HID=.*$", WOLF, re.M).group(0)])


class TestManifests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.sa = self.home / ".steam/debian-installation/steamapps"
        (self.sa / "common/Installed Game").mkdir(parents=True)
        for appid, folder in ((10, "Installed Game"), (20, "Cloud Game")):
            (self.sa / f"appmanifest_{appid}.acf").write_text(
                f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"installdir"\t\t"{folder}"\n}}\n')
        (self.sa / "libraryfolders.vdf").write_text("x")

    def bash(self, script, sg):
        # u — от пользователя (здесь просто выполнить), log — в вывод; F — GNU find, на Mac его нет: только имена
        pre = f'''
DH="{self.home}"; SR=.steam/debian-installation; SG={sg}
u() {{ "$@"; }}
log() {{ echo "LOG $*"; }}
{FUNCS}
F() {{ find "$@" -print 2>/dev/null || true; }}
'''
        r = subprocess.run(["bash", "-c", pre + script], capture_output=True, text=True, cwd=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def listing(self, sg):
        return self.bash('cd "$DH"; ls_ steam-state', sg)

    def test_manifests_in_cloud_whatever_the_toggle(self):
        for sg in (0, 1):
            out = self.listing(sg)
            self.assertIn("appmanifest_20.acf", out, f"SG={sg}")
            self.assertIn("libraryfolders.vdf", out)

    def test_sync_off_hides_missing_games_but_keeps_them_in_cloud(self):
        out = self.bash("acf_hide", 0)
        self.assertIn("спрятаны", out)
        self.assertTrue((self.sa / "appmanifest_10.acf").exists())            # файлы есть — Steam видит
        self.assertFalse((self.sa / "appmanifest_20.acf").exists())           # файлов нет — Steam не качает сам
        self.assertTrue((self.sa / ".vastgame-hidden/appmanifest_20.acf").exists())
        self.assertIn(".vastgame-hidden/appmanifest_20.acf", self.listing(0))  # и уедет в облако

    def test_sync_on_brings_them_back(self):
        self.bash("acf_hide", 0)
        out = self.bash("acf_unhide", 1)
        self.assertIn("возвращено паспортов — 1", out)
        self.assertTrue((self.sa / "appmanifest_20.acf").exists())
        self.assertFalse((self.sa / ".vastgame-hidden/appmanifest_20.acf").exists())

    def test_fresh_manifest_wins_over_hidden(self):
        self.bash("acf_hide", 0)
        (self.sa / "appmanifest_20.acf").write_text("new")                   # игру поставили заново
        self.bash("acf_unhide", 1)
        self.assertEqual((self.sa / "appmanifest_20.acf").read_text(), "new")
        self.assertFalse((self.sa / ".vastgame-hidden/appmanifest_20.acf").exists())


if __name__ == "__main__":
    unittest.main()

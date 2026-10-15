"""[2026-10-15] Поправка или целиком: удалённое тоже считается изменением (wolf pack, delsz). Живой случай: после уборки
1.6 ГБ тестовых программ из «диска C:» Avatar Legends в облаке осталась основа 1.8 ГБ + поправка с удалением."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_game_select import WOLF, fn  # noqa: E402


def man(*rows):
    return b"".join(f"{p}\t1700000000\t{n}".encode() + b"\0" for p, n in rows)


class TestDeletedCounts(unittest.TestCase):
    def run_sz(self, manifest, deleted):
        d = Path(tempfile.mkdtemp())
        (d / "m").write_bytes(manifest)
        (d / "del").write_bytes(b"".join(p.encode() + b"\0" for p in deleted))
        script = fn("sz") + "\n" + fn("delsz") + f'\necho "$(sz {d}/m) $(delsz {d}/m {d}/del)"'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout.split()

    def test_deleted_bytes(self):
        m = man(("drive_c/Program Files/Vortex/Vortex.exe", 900), ("drive_c/windows/a.dll", 500), ("saves/x", 100))
        self.assertEqual(self.run_sz(m, ["drive_c/Program Files/Vortex/Vortex.exe"]), ["1500", "900"])
        self.assertEqual(self.run_sz(m, []), ["1500", "0"])
        self.assertEqual(self.run_sz(m, ["нет такого"]), ["1500", "0"])

    def test_pack_counts_deleted(self):
        # решение «поправка» — по новому/изменённому И удалённому
        self.assertIn('[ $(( ($(sz "$w/d") + $(delsz "$S/m/$n" "$w/.wolf-del.$n")) * 4 )) -lt "$(sz "$S/m/$n")" ] && f=0',
                      WOLF)


if __name__ == "__main__":
    unittest.main()

# tests/test_mods_storage.py
# Проверка mods_pack и mods_apply на реальных файлах и изолированном хранилище.
# Сценарии:
# 1. Неудачная повторная выгрузка под тем же именем.
# 2. Неудачная выгрузка после переименования игры.
# 3. Загрузка завершилась, но вызывающая сторона получила ошибку (потеря ответа); затем штатный повтор.

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_game_select import TMP, WOLF, fn, heredoc

RCLONE = shutil.which("rclone") or next((p for p in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone")
                                         if os.path.exists(p)), None)
ZSTD = shutil.which("zstd") or next((p for p in ("/opt/homebrew/bin/zstd", "/usr/local/bin/zstd")
                                     if os.path.exists(p)), None)

PY_MODS = TMP / "wolf-mods.py"
if not PY_MODS.exists():
    PY_MODS.write_text(heredoc("wolf-mods.py", "MODS"))


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(root: Path) -> dict:
    """Возвращает {rel_path: sha256_hex} для всех регулярных файлов в каталоге."""
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = Path(d) / f
            if not p.is_symlink() and p.is_file():
                out[str(p.relative_to(root))] = sha256_of_file(p)
            elif p.is_symlink():
                out[str(p.relative_to(root))] = f"SYMLINK:{os.readlink(p)}"
    return out


def make_vanilla(root: Path):
    """Создаёт чистую игру, как её ставит Steam."""
    if root.exists():
        shutil.rmtree(root)
    (root / "bin").mkdir(parents=True)
    (root / "bin/game.exe").write_bytes(b"VANILLA_GAME_EXE_BINARY_V1" * 50)
    (root / "data").mkdir()
    (root / "data/textures.pak").write_bytes(b"VANILLA_TEXTURES_CONTENT" * 200)
    (root / "data/intro.bik").write_bytes(b"VANILLA_INTRO_VIDEO" * 10)
    return root


@unittest.skipUnless(RCLONE and ZSTD, "требуются rclone и zstd")
class TestModsStorageScenarios(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="mods_stor_test_"))
        self.cloud = self.d / "cloud"
        self.cloud.mkdir()
        self.tmp = self.d / "tmp"
        self.tmp.mkdir()
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.state = self.d / "state"
        self.state.mkdir()
        for s in ("st", "van", "x"):
            (self.state / s).mkdir()

        self.home = self.d / "home"
        self.steamapps = self.home / "sa" / "steamapps"
        (self.steamapps / "common").mkdir(parents=True)

        self.rclone_hook = self.d / "rclone_hook.sh"
        self.rclone_hook.write_text("#!/bin/bash\n:\n")
        self.rclone_hook.chmod(0o755)

        # Создаём обёртку rclone, которая может вызывать rclone_hook перед/после операций
        self.rclone_bin = self.bin / "rclone"
        self.rclone_bin.write_text(f'''#!/bin/bash
if [ -f "{self.rclone_hook}" ]; then
  . "{self.rclone_hook}"
fi
exec "{RCLONE}" "$@"
''')
        self.rclone_bin.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write_acf(self, appid: int, name: str, folder: str, buildid: str = "77"):
        acf = self.steamapps / f"appmanifest_{appid}.acf"
        acf.write_text(f'''"AppState"
{{
\t"appid"\t\t"{appid}"
\t"name"\t\t"{name}"
\t"buildid"\t\t"{buildid}"
\t"installdir"\t\t"{folder}"
}}
''')

    def set_hook(self, bash_code: str):
        self.rclone_hook.write_text(f"#!/bin/bash\n{bash_code}\n")

    def clear_hook(self):
        self.rclone_hook.write_text("#!/bin/bash\n:\n")

    def run_bash(self, script: str) -> tuple[int, str, str]:
        env_path = f"{self.bin}:{os.environ.get('PATH', '')}"
        log_file = self.d / "wolf.log"
        pack_code = fn("mods_pack").replace("/usr/local/bin/wolf-mods.py", str(PY_MODS)).replace("/var/log/wolf.log", str(log_file))
        apply_code = fn("mods_apply").replace("/usr/local/bin/wolf-mods.py", str(PY_MODS)).replace("/var/log/wolf.log", str(log_file))

        full_script = f'''
set -u
export PATH="{env_path}"
R="{self.cloud}"
S="{self.state}"
T="{self.tmp}"
DH="{self.home}"
SR=sa
LS="{self.tmp}/ls"
st() {{ echo "$2|$3" > "$S/st/$1"; }}
log() {{ echo "$*"; }}
rd() {{ cat "$1" 2>/dev/null; }}
u() {{ "$@"; }}
rc() {{ rclone "$@"; }}
refresh_ls() {{ rc lsf --files-only -F phs --hash MD5 -s $'\t' "$R" > "$LS"; }}
rl() {{ rc lsf --files-only -F phs --hash MD5 -s $'\t' "$R"; }}
names() {{ sed -n -E 's/\\.tar\\.zst(\\.parts)?\\t.*//p' "$LS" | awk '!s[$0]++' | grep -E "$1"; }}
forget() {{ rc deletefile "$R/$1.tar.zst"; }}

{pack_code}
{apply_code}

refresh_ls
{script}
'''
        p = subprocess.run(["bash", "-c", full_script], capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout, p.stderr

    def run_pack(self, appid: int, folder: str) -> tuple[int, str, str]:
        return self.run_bash(f"mods_pack {appid} {folder}")

    def run_apply(self, appid: int, target_folder: str) -> tuple[int, str, str]:
        script = f'''
echo 1 > "$S/play.mods"
mods_apply {appid} {target_folder}
'''
        return self.run_bash(script)

    def test_scenario1_failure_before_transfer_same_name(self):
        """1. Отказ до начала передачи под тем же именем (hook завершает команду до запуска rclone)."""
        appid, folder, name = 10, "Ten", "Ten"
        self.write_acf(appid, name, folder)
        game = self.steamapps / "common" / folder
        make_vanilla(game)

        # 1. Снимок чистой игры
        van_snap = self.state / "van" / f"{appid}.tsv"
        p = subprocess.run([sys.executable, str(PY_MODS), "snap", str(game), str(van_snap)], capture_output=True)
        self.assertEqual(p.returncode, 0)

        # 2. Установка модов V1
        (game / "BepInEx/plugins").mkdir(parents=True)
        (game / "BepInEx/plugins/Mod.dll").write_bytes(b"MOD_V1_DLL_CONTENT_AAABBB")
        (game / "data/textures.pak").write_bytes(b"MOD_V1_TEXTURES_NEW_TEXTURES_ZZZ")
        expected_v1 = tree_sha256(game)

        # 3. Первая успешная выгрузка V1
        code, out, err = self.run_pack(appid, folder)
        self.assertEqual(code, 0, f"pack v1 failed: {err}\n{out}")
        self.assertTrue((self.cloud / f"mod--{appid}--{name}.tar.zst").exists())
        fp_v1 = (self.state / "van" / f"{appid}.fp").read_text().strip()
        self.assertTrue(fp_v1)
        st_v1 = (self.state / "st" / f"mod--{appid}--{name}").read_text().strip()
        self.assertIn("ok|моды выгружены", st_v1)

        # Проверка восстановления V1 в чистое дерево
        clean1 = self.steamapps / "common" / "Clean1"
        make_vanilla(clean1)
        code, out, err = self.run_apply(appid, "Clean1")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_sha256(clean1), expected_v1)

        # 4. Обновление модов до V2 на машине автора
        (game / "BepInEx/plugins/Mod.dll").write_bytes(b"MOD_V2_EXPANDED_DLL_XXX")
        (game / "BepInEx/config.ini").write_bytes(b"SETTINGS=2\n")
        (game / "data/intro.bik").unlink()
        expected_v2 = tree_sha256(game)

        # 5. Выгрузка V2 с отказом загрузки (rclone copyto падает до/во время выгрузки)
        # Моделируем сетевой отказ rclone copyto:
        self.set_hook('''
if [[ " $* " == *" copyto "* ]]; then
  echo "HOOK: inject failure on copyto" >&2
  exit 1
fi
''')
        code, out, err = self.run_pack(appid, folder)
        self.assertNotEqual(code, 0)
        self.assertIn("err|выгрузка модов не удалась", (self.state / "st" / f"mod--{appid}--{name}").read_text())
        # Отпечаток остался старым V1!
        self.assertEqual((self.state / "van" / f"{appid}.fp").read_text().strip(), fp_v1)

        # 6. Проверка хранилища и данных после сбоя выгрузки:
        # В облаке остался файл V1!
        self.assertTrue((self.cloud / f"mod--{appid}--{name}.tar.zst").exists())
        clean2 = self.steamapps / "common" / "Clean2"
        make_vanilla(clean2)
        code, out, err = self.run_apply(appid, "Clean2")
        self.assertEqual(code, 0, err)
        # Восстановленное дерево должно ТОЧНО соответствовать V1 (предыдущая восстанавливаемая версия сохранилась)
        self.assertEqual(tree_sha256(clean2), expected_v1)
        self.assertNotEqual(tree_sha256(clean2), expected_v2)

        # 7. Штатный повтор выгрузки без ошибки
        self.clear_hook()
        code, out, err = self.run_pack(appid, folder)
        self.assertEqual(code, 0, f"retry pack failed: {err}\n{out}")
        self.assertIn("ok|моды выгружены", (self.state / "st" / f"mod--{appid}--{name}").read_text())
        fp_v2 = (self.state / "van" / f"{appid}.fp").read_text().strip()
        self.assertNotEqual(fp_v1, fp_v2)

        # 8. Проверка восстановления актуальной версии V2 после повтора
        clean3 = self.steamapps / "common" / "Clean3"
        make_vanilla(clean3)
        code, out, err = self.run_apply(appid, "Clean3")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_sha256(clean3), expected_v2)

    def test_scenario_interrupted_transfer_same_name(self):
        """Прерывание активной передачи большого архива V2 (локальный backend rclone)."""
        appid, folder, name = 10, "Ten", "Ten"
        self.write_acf(appid, name, folder)
        game = self.steamapps / "common" / folder
        make_vanilla(game)

        # 1. Снимок чистой игры и установка модов V1
        van_snap = self.state / "van" / f"{appid}.tsv"
        subprocess.run([sys.executable, str(PY_MODS), "snap", str(game), str(van_snap)], check=True)
        (game / "BepInEx/plugins").mkdir(parents=True)
        (game / "BepInEx/plugins/Mod.dll").write_bytes(b"MOD_V1_SMALL_DLL")
        expected_v1 = tree_sha256(game)

        # Выгрузка V1
        code, out, err = self.run_pack(appid, folder)
        self.assertEqual(code, 0, err)
        fp_v1 = (self.state / "van" / f"{appid}.fp").read_text().strip()
        v1_stat = (self.cloud / f"mod--{appid}--{name}.tar.zst").stat()

        # 2. Моды V2: большой архив (~2 МБ несжимаемых данных) для надежного отслеживания передачи
        (game / "BepInEx/plugins/Mod.dll").write_bytes(os.urandom(2 * 1024 * 1024))
        expected_v2 = tree_sha256(game)

        # 3. Hook с ограничением скорости и прерыванием активной передачи:
        # - запускает rclone с --local-no-clone --bwlimit 100k в фоне
        # - ожидает подтверждения начала передачи (появление .partial файла в cloud)
        # - отправляет SIGTERM процессу rclone
        # - фиксирует факт прерывания и возвращает код ошибки
        cloud_dir = str(self.cloud)
        hook_code = f'''
if [[ " $* " == *" copyto "* ]]; then
  "{RCLONE}" "$@" --local-no-clone --bwlimit 100k &
  RPID=$!
  for i in $(seq 1 50); do
    sleep 0.05
    if ls "{cloud_dir}"/*.partial 1>/dev/null 2>&1; then
      PART_NAME=$(ls "{cloud_dir}"/*.partial | head -1)
      echo "HOOK: transfer started, confirmed partial file: $PART_NAME" >&2
      kill -TERM "$RPID"
      wait "$RPID" 2>/dev/null
      RC=$?
      echo "HOOK: rclone terminated with signal, exit code: $RC" >&2
      exit 1
    fi
  done
  kill -9 "$RPID" 2>/dev/null
  exit 1
fi
'''
        self.set_hook(hook_code)

        # 4. Выгрузка V2 прерывается в процессе
        code, out, err = self.run_pack(appid, folder)
        self.assertNotEqual(code, 0)
        self.assertIn("HOOK: transfer started, confirmed partial file:", err)
        self.assertIn("HOOK: rclone terminated with signal", err)
        self.assertIn("err|выгрузка модов не удалась", (self.state / "st" / f"mod--{appid}--{name}").read_text())
        # Отпечаток остался прежним V1
        self.assertEqual((self.state / "van" / f"{appid}.fp").read_text().strip(), fp_v1)

        # 5. Проверка фактического состояния хранилища:
        # Целевой файл V1 остался на месте с исходным размером и содержимым!
        current_dst = self.cloud / f"mod--{appid}--{name}.tar.zst"
        self.assertTrue(current_dst.exists())
        self.assertEqual(current_dst.stat().st_size, v1_stat.st_size)

        # 6. Восстановление через mods_apply до повтора:
        clean_mid = self.steamapps / "common" / "CleanMid"
        make_vanilla(clean_mid)
        code, out, err = self.run_apply(appid, "CleanMid")
        self.assertEqual(code, 0, err)
        # Восстановилась версия V1!
        self.assertEqual(tree_sha256(clean_mid), expected_v1)

        # 7. Штатный повтор без ошибки:
        self.clear_hook()
        code, out, err = self.run_pack(appid, folder)
        self.assertEqual(code, 0, err)
        self.assertIn("ok|моды выгружены", (self.state / "st" / f"mod--{appid}--{name}").read_text())
        fp_v2 = (self.state / "van" / f"{appid}.fp").read_text().strip()
        self.assertNotEqual(fp_v1, fp_v2)

        # 8. Проверка восстановления актуальной версии V2 после повтора:
        clean_final = self.steamapps / "common" / "CleanFinal"
        make_vanilla(clean_final)
        code, out, err = self.run_apply(appid, "CleanFinal")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_sha256(clean_final), expected_v2)

    def test_scenario2_failure_before_transfer_rename(self):
        """2. Отказ до начала передачи после переименования игры (hook завершает команду до запуска rclone)."""
        appid, folder1, name1 = 10, "OldTen", "OldTen"
        self.write_acf(appid, name1, folder1)
        game1 = self.steamapps / "common" / folder1
        make_vanilla(game1)

        # Снимок и установка модов V1
        van_snap1 = self.state / "van" / f"{appid}.tsv"
        subprocess.run([sys.executable, str(PY_MODS), "snap", str(game1), str(van_snap1)], check=True)
        (game1 / "BepInEx/plugins").mkdir(parents=True)
        (game1 / "BepInEx/plugins/Mod.dll").write_bytes(b"V1_DLL_OLD")
        expected_v1 = tree_sha256(game1)

        # Первая выгрузка под именем OldTen
        code, out, err = self.run_pack(appid, folder1)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.cloud / f"mod--{appid}--{name1}.tar.zst").exists())

        # Переименование игры в NewTen и обновление модов до V2
        folder2, name2 = "NewTen", "NewTen"
        self.write_acf(appid, name2, folder2)
        game2 = self.steamapps / "common" / folder2
        make_vanilla(game2)
        # Снимок для NewTen
        van_snap2 = self.state / "van" / f"{appid}.tsv"
        subprocess.run([sys.executable, str(PY_MODS), "snap", str(game2), str(van_snap2)], check=True)
        (game2 / "BepInEx/plugins").mkdir(parents=True)
        (game2 / "BepInEx/plugins/Mod.dll").write_bytes(b"V2_DLL_NEW_NAME")
        (game2 / "new_mod_asset.dat").write_bytes(b"NEW_ASSET")
        expected_v2 = tree_sha256(game2)

        # Сбойная выгрузка NewTen
        self.set_hook('''
if [[ " $* " == *" copyto "* ]]; then
  echo "HOOK: fail upload of renamed mod" >&2
  exit 1
fi
''')
        code, out, err = self.run_pack(appid, folder2)
        self.assertNotEqual(code, 0)
        self.assertIn("err|выгрузка модов не удалась", (self.state / "st" / f"mod--{appid}--{name2}").read_text())

        # Проверка хранилища: старый архив OldTen НЕ должен быть удалён!
        self.assertTrue((self.cloud / f"mod--{appid}--{name1}.tar.zst").exists())
        self.assertFalse((self.cloud / f"mod--{appid}--{name2}.tar.zst").exists())

        # Проверка восстановления на чистой машине: восстанавливается сохранённый OldTen (V1)
        clean_old = self.steamapps / "common" / "CleanOld"
        make_vanilla(clean_old)
        code, out, err = self.run_apply(appid, "CleanOld")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_sha256(clean_old), expected_v1)

        # Штатный повтор выгрузки NewTen
        self.clear_hook()
        code, out, err = self.run_pack(appid, folder2)
        self.assertEqual(code, 0, err)
        self.assertIn("ok|моды выгружены", (self.state / "st" / f"mod--{appid}--{name2}").read_text())

        # После успешного повтора: старый архив OldTen удалён, новый NewTen на месте!
        self.assertFalse((self.cloud / f"mod--{appid}--{name1}.tar.zst").exists())
        self.assertTrue((self.cloud / f"mod--{appid}--{name2}.tar.zst").exists())

        # Проверка восстановления актуальной версии V2 в NewTen
        clean_new = self.steamapps / "common" / "CleanNew"
        make_vanilla(clean_new)
        code, out, err = self.run_apply(appid, "CleanNew")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_sha256(clean_new), expected_v2)

    def test_scenario3_lost_response_then_normal_retry(self):
        """3. Загрузка завершилась, но вызывающая сторона получила ошибку (потеря ответа); затем штатный повтор."""
        appid, folder, name = 10, "Ten", "Ten"
        self.write_acf(appid, name, folder)
        game = self.steamapps / "common" / folder
        make_vanilla(game)

        # Снимок и установка модов V1
        van_snap = self.state / "van" / f"{appid}.tsv"
        subprocess.run([sys.executable, str(PY_MODS), "snap", str(game), str(van_snap)], check=True)
        (game / "BepInEx/plugins").mkdir(parents=True)
        (game / "BepInEx/plugins/Mod.dll").write_bytes(b"MOD_V1_DATA")
        expected_v1 = tree_sha256(game)

        # Выгрузка V1
        code, out, err = self.run_pack(appid, folder)
        self.assertEqual(code, 0, err)
        fp_v1 = (self.state / "van" / f"{appid}.fp").read_text().strip()

        # Обновление до V2
        (game / "BepInEx/plugins/Mod.dll").write_bytes(b"MOD_V2_DATA_AFTER_LOST_RESPONSE")
        (game / "extra_patch.bin").write_bytes(b"PATCH_BYTES_12345")
        expected_v2 = tree_sha256(game)

        # Моделируем потерю ответа: rclone выполняет copyto (файл физически записывается в cloud),
        # но затем завершается с кодом 1 (сеть оборвалась при получении HTTP 200).
        self.set_hook(f'''
if [[ " $* " == *" copyto "* ]]; then
  "{RCLONE}" "$@"
  echo "HOOK: bytes transferred successfully, but response dropped" >&2
  exit 1
fi
''')
        code, out, err = self.run_pack(appid, folder)
        self.assertNotEqual(code, 0)
        self.assertIn("err|выгрузка модов не удалась", (self.state / "st" / f"mod--{appid}--{name}").read_text())
        # Отпечаток .fp НЕ обновился (остался V1)
        self.assertEqual((self.state / "van" / f"{appid}.fp").read_text().strip(), fp_v1)

        # Проверка хранилища: физически в хранилище файл уже обновился до V2
        self.assertTrue((self.cloud / f"mod--{appid}--{name}.tar.zst").exists())

        # Если клиент сейчас скачает моды:
        clean_mid = self.steamapps / "common" / "CleanMid"
        make_vanilla(clean_mid)
        code, out, err = self.run_apply(appid, "CleanMid")
        self.assertEqual(code, 0, err)
        # Поскольку файл в облаке целостен, он успешно распаковывается в V2!
        self.assertEqual(tree_sha256(clean_mid), expected_v2)

        # Теперь штатный повтор на машине автора:
        # Поскольку .fp не обновился, diff видит расхождение и заново вызывает mods_pack
        self.clear_hook()
        code, out, err = self.run_pack(appid, folder)
        self.assertEqual(code, 0, f"retry failed: {err}\n{out}")
        self.assertIn("ok|моды выгружены", (self.state / "st" / f"mod--{appid}--{name}").read_text())
        fp_v2 = (self.state / "van" / f"{appid}.fp").read_text().strip()
        self.assertNotEqual(fp_v1, fp_v2)

        # Проверка восстановления после повтора:
        clean_final = self.steamapps / "common" / "CleanFinal"
        make_vanilla(clean_final)
        code, out, err = self.run_apply(appid, "CleanFinal")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_sha256(clean_final), expected_v2)

    def test_scenario3b_lost_response_with_rename_then_normal_retry(self):
        """3b. Загрузка переименованного мода завершилась, но ответ потерян; затем штатный повтор."""
        appid, folder1, name1 = 10, "Alpha", "Alpha"
        self.write_acf(appid, name1, folder1)
        game1 = self.steamapps / "common" / folder1
        make_vanilla(game1)

        # Выгрузка V1 под именем Alpha
        van_snap1 = self.state / "van" / f"{appid}.tsv"
        subprocess.run([sys.executable, str(PY_MODS), "snap", str(game1), str(van_snap1)], check=True)
        (game1 / "BepInEx/plugins").mkdir(parents=True)
        (game1 / "BepInEx/plugins/Mod.dll").write_bytes(b"V1_ALPHA")
        expected_v1 = tree_sha256(game1)
        code, out, err = self.run_pack(appid, folder1)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.cloud / f"mod--{appid}--{name1}.tar.zst").exists())

        # Переименование в Bravo и обновление до V2
        folder2, name2 = "Bravo", "Bravo"
        self.write_acf(appid, name2, folder2)
        game2 = self.steamapps / "common" / folder2
        make_vanilla(game2)
        van_snap2 = self.state / "van" / f"{appid}.tsv"
        subprocess.run([sys.executable, str(PY_MODS), "snap", str(game2), str(van_snap2)], check=True)
        (game2 / "BepInEx/plugins").mkdir(parents=True)
        (game2 / "BepInEx/plugins/Mod.dll").write_bytes(b"V2_BRAVO")
        expected_v2 = tree_sha256(game2)

        # Моделируем потерю ответа: copyto для Bravo выполняется, но rclone завершается с кодом 1
        self.set_hook(f'''
if [[ " $* " == *" copyto "* ]]; then
  "{RCLONE}" "$@"
  echo "HOOK: Bravo bytes transferred successfully, but response dropped" >&2
  exit 1
fi
''')
        code, out, err = self.run_pack(appid, folder2)
        self.assertNotEqual(code, 0)
        self.assertIn("err|выгрузка модов не удалась", (self.state / "st" / f"mod--{appid}--{name2}").read_text())

        # В промежуточном состоянии: в облаке есть оба архива (Alpha и Bravo)
        self.assertTrue((self.cloud / f"mod--{appid}--{name1}.tar.zst").exists())
        self.assertTrue((self.cloud / f"mod--{appid}--{name2}.tar.zst").exists())

        # Штатный повтор на машине автора:
        self.clear_hook()
        code, out, err = self.run_pack(appid, folder2)
        self.assertEqual(code, 0, f"retry failed: {err}\n{out}")
        self.assertIn("ok|моды выгружены", (self.state / "st" / f"mod--{appid}--{name2}").read_text())

        # После успешного повтора: старый архив Alpha удалён, Bravo остался один!
        self.assertFalse((self.cloud / f"mod--{appid}--{name1}.tar.zst").exists())
        self.assertTrue((self.cloud / f"mod--{appid}--{name2}.tar.zst").exists())

        # Восстановление на чистой машине восстанавливает V2 (Bravo)
        clean_bravo = self.steamapps / "common" / "CleanBravo"
        make_vanilla(clean_bravo)
        code, out, err = self.run_apply(appid, "CleanBravo")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_sha256(clean_bravo), expected_v2)


if __name__ == "__main__":
    unittest.main()

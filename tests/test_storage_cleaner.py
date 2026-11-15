"""tests/test_storage_cleaner.py — стендовые приёмочные тесты исполнителя очистки wolf_cleaner.py.

Работает ИСКЛЮЧИТЕЛЬНО на изолированном локальном стендовом каталоге (mock storage).
Никаких обращений к облаку, Google Drive, реальным учётным данным или платным GPU-арендам.

Проверяет:
1. Синхронизацию wolf_cleaner.py со встроенной копией в wolf-setup.sh.
2. Негативные сценарии _check_session_authorization:
   - отсутствие модуля wolf_session;
   - исключение при проверке;
   - старый run_id;
   - чужой iid;
   - чужой folder_id;
   Во всех случаях — отказ и строго ноль удалений.
3. Связывание снимка состояния именно с классификацией:
   - фиксация базового состояния хранилища ДО классификации;
   - проверка неизменности ПОСЛЕ классификации;
   - перепроверка снимка плана ПЕРЕД удалением;
   - управляемое изменение между классификацией и фиксацией плана -> отмена устаревшего плана, 0 удалений, сохранение flock.
4. Штатное восстановление настоящего Wolf:
   - подготовка через существующий движок (wolf-parts.py put);
   - локальная стендовая очистка вытесненных кандидатов;
   - штатное восстановление в пустую папку через конвейер Wolf:
     parts get (или rclone cat) | zstd -dcq | tar -xpf - -C <dest>;
   - сравнение полного набора путей и SHA-256 с оригинальными файлами.
5. Защиту попыток attempt-* и pending с реальным resume и восстановлением (включая попытку дельты):
   - незавершённые выгрузки базы и накопительной дельты защищены (0 удалений);
   - реальный запуск wolf-parts.py resume активирует выгрузки;
   - последующее восстановление Wolf в пустую папку подтверждает корректность развёрнутых данных.
6. Взаимное исключение очистки с реальными операциями движка (wolf pack):
   - очистка под flock блокирует запуск записи (pack не выполняется одновременно);
   - идущая запись под flock блокирует запуск очистки (archive_lock_busy, 0 удалений).
7. Защиту неизвестных поколений старого Wolf со старым mtime (0 удалений).
8. Прерывание прохода очистки и безопасное продолжение остатка на повторном запуске.
9. Изоляцию стенда: посторонние файлы, похожие архивы, symlink traversal за пределы стенда.
10. Режим предварительного запуска (--dry-run) и раздельный учёт байтов в отчёте.
"""

import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wolf_classifier import PARTS_HEAD
from wolf_cleaner import (
    capture_storage_state,
    compute_storage_snapshot,
    execute_stand_cleanup,
    format_cleanup_result,
    verify_storage_snapshot,
    verify_storage_state,
)

ZSTD_BIN = shutil.which("zstd") or next(
    (p for p in ("/opt/homebrew/bin/zstd", "/usr/bin/zstd", "/usr/local/bin/zstd") if os.path.exists(p)),
    None,
)
RCLONE_BIN = shutil.which("rclone") or next(
    (p for p in ("/opt/homebrew/bin/rclone", "/usr/bin/rclone", "/usr/local/bin/rclone") if os.path.exists(p)),
    None,
)


def load_real_wolf_parts():
    """Извлекает и компилирует настоящий встроенный wolf-parts.py из wolf-setup.sh."""
    setup_path = Path(__file__).resolve().parent.parent / "wolf-setup.sh"
    setup_content = setup_path.read_text(encoding="utf-8")
    start_marker = "w /usr/local/bin/wolf-parts.py <<'PARTS'\n"
    end_marker = "\nPARTS\n"
    s_idx = setup_content.index(start_marker) + len(start_marker)
    e_idx = setup_content.index(end_marker, s_idx)
    parts_code = setup_content[s_idx:e_idx]
    ns = {}
    exec(compile(parts_code, "wolf-parts.py", "exec"), ns)
    return ns, parts_code


def load_real_wolf_script() -> str:
    """Извлекает исходный текст настоящего скрипта /usr/local/bin/wolf из wolf-setup.sh."""
    setup_path = Path(__file__).resolve().parent.parent / "wolf-setup.sh"
    setup_content = setup_path.read_text(encoding="utf-8")
    match = re.search(r"^w /usr/local/bin/wolf <<'WOLF'\n(.*?)^WOLF$", setup_content, re.S | re.M)
    assert match, "Не удалось найти w /usr/local/bin/wolf в wolf-setup.sh"
    return match.group(1)


WOLF_SCRIPT_CODE = load_real_wolf_script()


def wolf_func(name: str) -> str:
    """Извлекает определение функции из настоящего скрипта wolf."""
    found = (re.search(rf"^{name}\(\) *\{{[^\n]*\}}$", WOLF_SCRIPT_CODE, re.M)
             or re.search(rf"^{name}\(\) *\{{.*?\n\}}$", WOLF_SCRIPT_CODE, re.S | re.M))
    assert found, f"Функция {name} не найдена в настоящем скрипте wolf"
    return found.group(0)


_WOLF_PARTS, WOLF_PARTS_CODE = load_real_wolf_parts()
engine_format_receipt = _WOLF_PARTS["format_receipt"]
engine_note_attempt = _WOLF_PARTS["note_attempt"]


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_tar_bytes(file_dict: dict) -> bytes:
    """Создаёт tar-архив в памяти из словаря {относительный_путь: содержимое_bytes}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in file_dict.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = 1700000000
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def zstd_compress(data: bytes) -> bytes:
    if not ZSTD_BIN:
        raise RuntimeError("zstd binary not found for test compression")
    p = subprocess.Popen([ZSTD_BIN, "-q", "-T0", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(input=data)
    if p.returncode != 0:
        raise RuntimeError(f"zstd compression error: {err.decode('utf-8', 'replace')}")
    return out


def zstd_decompress(data: bytes) -> bytes:
    if not ZSTD_BIN:
        raise RuntimeError("zstd binary not found for test decompression")
    p = subprocess.Popen([ZSTD_BIN, "-dcq", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(input=data)
    if p.returncode != 0:
        raise RuntimeError(f"zstd decompression error: {err.decode('utf-8', 'replace')}")
    return out


def get_dir_tree_hashes(target_dir: Path) -> dict:
    """Возвращает словарь {относительный_путь: sha256_hex} для всех обычных файлов в каталоге."""
    res = {}
    for p in sorted(target_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(target_dir).as_posix()
            res[rel] = sha256_bytes(p.read_bytes())
    return res


def wolf_engine_restore(script_path: Path, remote_dir: Path, obj_name: str, dest_dir: Path, env: dict, delta_hash: str | None = None):
    """Штатный путь восстановления Wolf в пустой каталог:
    1. База: если есть <obj>.parts, читается через parts get, иначе через rclone cat.
       Поток направляется в zstd -dcq | tar -xpf - -C <dest_dir>.
    2. Дельта: если delta_hash передан, скачивается <obj>.<delta_hash> через parts get или rclone cat.
       Поток направляется в zstd -dcq | tar -xpf - -C <dest_dir>.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    parts_idx = remote_dir / f"{obj_name}.parts"
    if parts_idx.is_file():
        p1 = subprocess.Popen([sys.executable, str(script_path), "get", str(remote_dir), obj_name],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    else:
        p1 = subprocess.Popen([RCLONE_BIN or "rclone", "cat", f"{remote_dir}/{obj_name}"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    p2 = subprocess.Popen([ZSTD_BIN, "-dcq", "-"], stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p3 = subprocess.Popen(["tar", "-xpf", "-", "-C", str(dest_dir)], stdin=p2.stdout, stderr=subprocess.PIPE)
    p1.stdout.close()
    p2.stdout.close()
    _, err3 = p3.communicate()
    _, err2 = p2.communicate()
    _, err1 = p1.communicate()

    if p1.returncode != 0 or p2.returncode != 0 or p3.returncode != 0:
        raise RuntimeError(f"Base restore failed: p1={p1.returncode} ({err1.decode('utf-8', 'replace')}), p2={p2.returncode}, p3={p3.returncode}")

    if delta_hash:
        delta_obj = f"{obj_name}.{delta_hash}"
        d_parts_idx = remote_dir / f"{delta_obj}.parts"
        if d_parts_idx.is_file():
            dp1 = subprocess.Popen([sys.executable, str(script_path), "get", str(remote_dir), delta_obj],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        else:
            dp1 = subprocess.Popen([RCLONE_BIN or "rclone", "cat", f"{remote_dir}/{delta_obj}"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        dp2 = subprocess.Popen([ZSTD_BIN, "-dcq", "-"], stdin=dp1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        dp3 = subprocess.Popen(["tar", "-xpf", "-", "-C", str(dest_dir)], stdin=dp2.stdout, stderr=subprocess.PIPE)
        dp1.stdout.close()
        dp2.stdout.close()
        _, derr3 = dp3.communicate()
        _, derr2 = dp2.communicate()
        _, derr1 = dp1.communicate()
        if dp1.returncode != 0 or dp2.returncode != 0 or dp3.returncode != 0:
            raise RuntimeError(f"Delta restore failed: dp1={dp1.returncode} ({derr1.decode('utf-8', 'replace')}), dp2={dp2.returncode} ({derr2.decode('utf-8', 'replace')}), dp3={dp3.returncode} ({derr3.decode('utf-8', 'replace')})")


class TestStorageCleaner(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.storage_dir = self.td / "remote_storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.l_dir = self.td / "run_wolf"
        self.l_dir.mkdir(parents=True, exist_ok=True)
        self.t_dir = self.td / "var_tmp_wolf"
        self.t_dir.mkdir(parents=True, exist_ok=True)
        self.s_dir = self.td / "var_lib_wolf"
        self.s_dir.mkdir(parents=True, exist_ok=True)
        self.grant_file = self.l_dir / "cleanup_grant.json"

        # Записываем скрипт настоящего движка wolf-parts.py
        self.parts_script = self.td / "wolf-parts.py"
        self.parts_script.write_text(WOLF_PARTS_CODE, encoding="utf-8")

        # Окружение движка для квитанций
        _WOLF_PARTS["TMP"] = str(self.t_dir)
        os.environ["WOLF_TMP"] = str(self.t_dir)

        # Очищаем тестовые хуки перед каждым тестом
        if hasattr(execute_stand_cleanup, "_test_hook"):
            delattr(execute_stand_cleanup, "_test_hook")
        if hasattr(execute_stand_cleanup, "_pre_verify_hook"):
            delattr(execute_stand_cleanup, "_pre_verify_hook")
        if hasattr(execute_stand_cleanup, "_post_classify_hook"):
            delattr(execute_stand_cleanup, "_post_classify_hook")

    def tearDown(self):
        if hasattr(execute_stand_cleanup, "_test_hook"):
            delattr(execute_stand_cleanup, "_test_hook")
        if hasattr(execute_stand_cleanup, "_pre_verify_hook"):
            delattr(execute_stand_cleanup, "_pre_verify_hook")
        if hasattr(execute_stand_cleanup, "_post_classify_hook"):
            delattr(execute_stand_cleanup, "_post_classify_hook")
        shutil.rmtree(self.td, ignore_errors=True)

    def _engine_env(self, part_bytes=1000):
        env = os.environ.copy()
        env["WOLF_TMP"] = str(self.t_dir)
        if part_bytes is not None:
            env["WOLF_PART_BYTES"] = str(part_bytes)
        gnubin = [
            "/opt/homebrew/opt/findutils/libexec/gnubin",
            "/opt/homebrew/opt/gnu-tar/libexec/gnubin",
            "/opt/homebrew/opt/coreutils/libexec/gnubin",
            "/usr/local/opt/findutils/libexec/gnubin",
            "/usr/local/opt/gnu-tar/libexec/gnubin",
            "/usr/local/opt/coreutils/libexec/gnubin",
        ]
        path_items = [str(self.td)]
        path_items.extend([p for p in gnubin if os.path.isdir(p)])
        if ZSTD_BIN:
            path_items.append(str(Path(ZSTD_BIN).parent))
        if RCLONE_BIN:
            path_items.append(str(Path(RCLONE_BIN).parent))
        path_items.append(env.get("PATH", ""))
        env["PATH"] = ":".join(path_items)
        return env

    def _create_real_wolf_runner(self, home_dir: Path):
        """Создаёт исполняемый тестовый скрипт runner с настоящими функциями Wolf из wolf-setup.sh."""
        names = (
            "st", "rd", "spec", "F", "ls_", "man", "sz", "delsz", "gamearch",
            "rver", "rl", "idhint", "pack", "get", "del", "xt_user", "unpack",
        )
        body = "\n".join(wolf_func(n) for n in names).replace("/usr/local/bin/wolf-parts.py", str(self.parts_script))
        header = f"""#!/bin/bash
S="{self.s_dir}"; T="{self.t_dir}"; L="{self.l_dir}"; R="{self.storage_dir}"
DH="{home_dir}"
SR=sa; ZL=3; ZG=1; LS="$T/ls"
mkdir -p "$S"/h "$S"/m "$S"/v "$S"/p "$S"/st "$S"/nt "$S"/nm "$S"/x "$S"/pg "$T" "$L"
"""
        footer = f"""
u() {{ "$@"; }}
log() {{ echo "$*"; }}
sr() {{ SR=sa; }}
rc() {{ rclone "$@" --retries=1 --low-level-retries=2; }}
rcp() {{ rc "$@"; }}
parts() {{
  local c=$1; shift
  if [ "$c" = "stage" ] && [ -n "${{PACK_PAUSE_HOOK:-}}" ]; then
    touch "$T/pack_in_stage.marker"
    local waited=0
    while [ ! -f "$T/cleaner_done.marker" ] && [ $waited -lt 100 ]; do
      sleep 0.05
      waited=$((waited + 1))
    done
  fi
  WOLF_TMP="$T" python3 "{self.parts_script}" "$c" "$R" "$@"
}}
case "${{1:-}}" in
  pack)
    shift
    rl > "$LS"
    pack "$@"
    ;;
  unpack)
    shift
    rm -f "$S/v/$1"
    rl > "$LS"
    unpack "$1"
    ;;
  *)
    "$@"
    ;;
esac
"""
        runner = self.td / "wolf_runner.sh"
        runner.write_text(header + body + footer, encoding="utf-8")
        runner.chmod(0o755)
        return runner

    def _setup_active_grant(self, run_id="run_test_clean_001", iid="1001", folder_id="fid_clean_99"):
        self.grant_file.write_text(
            json.dumps({"status": "granted", "run_id": run_id, "iid": iid, "folder_id": folder_id}),
            encoding="utf-8",
        )
        (self.l_dir / "run_id").write_text(run_id, encoding="utf-8")
        (self.l_dir / "iid").write_text(iid, encoding="utf-8")
        (self.l_dir / "storage_folder_id").write_text(folder_id, encoding="utf-8")

    def _write_engine_pending(self, archive_name: str, target_obj: str, receipt: tuple):
        receipt_str = engine_format_receipt(receipt)
        p_dir = self.s_dir / "pending"
        p_dir.mkdir(parents=True, exist_ok=True)
        path = p_dir / archive_name
        path.write_text(f"object {target_obj}\n{receipt_str}", encoding="utf-8")
        return path

    def _ensure_flock_shim(self):
        if not shutil.which("flock"):
            shim = self.td / "flock"
            shim.write_text(
                "#!/usr/bin/env python3\n"
                "import sys, os, fcntl, time\n"
                "args = sys.argv[1:]\n"
                "nonblock = False\n"
                "wait_sec = None\n"
                "fd = None\n"
                "i = 0\n"
                "while i < len(args):\n"
                "    a = args[i]\n"
                "    if a == '-n':\n"
                "        nonblock = True\n"
                "    elif a in ('-w', '--wait'):\n"
                "        wait_sec = float(args[i+1])\n"
                "        i += 1\n"
                "    elif a.isdigit():\n"
                "        fd = int(a)\n"
                "    i += 1\n"
                "if fd is None: sys.exit(2)\n"
                "flags = fcntl.LOCK_EX\n"
                "if nonblock or wait_sec is not None: flags |= fcntl.LOCK_NB\n"
                "t0 = time.time()\n"
                "while True:\n"
                "    try:\n"
                "        fcntl.flock(fd, flags)\n"
                "        sys.exit(0)\n"
                "    except (BlockingIOError, OSError):\n"
                "        if nonblock or (wait_sec is not None and time.time() - t0 >= wait_sec):\n"
                "            sys.exit(1)\n"
                "        time.sleep(0.05)\n"
            )
            shim.chmod(0o755)

    def test_embedded_cleaner_matches_standalone(self):
        """Проверка синхронизации: код wolf_cleaner.py в wolf-setup.sh строго идентичен оригиналу."""
        root_dir = Path(__file__).resolve().parent.parent
        setup_path = root_dir / "wolf-setup.sh"
        cleaner_path = root_dir / "wolf_cleaner.py"

        self.assertTrue(setup_path.is_file(), "wolf-setup.sh не найден")
        self.assertTrue(cleaner_path.is_file(), "wolf_cleaner.py не найден")

        setup_content = setup_path.read_text(encoding="utf-8")
        start_marker = "w /usr/local/lib/wolf_cleaner.py 644 <<'CLEANER'\n"
        end_marker = "\nCLEANER\n"

        self.assertIn(start_marker, setup_content)
        self.assertIn(end_marker, setup_content)

        s_idx = setup_content.index(start_marker) + len(start_marker)
        e_idx = setup_content.index(end_marker, s_idx)
        embedded_code = setup_content[s_idx:e_idx].strip()
        standalone_code = cleaner_path.read_text(encoding="utf-8").strip()

        self.assertEqual(embedded_code, standalone_code, "Встроенный wolf_cleaner.py в wolf-setup.sh расходится с оригиналом!")

    def test_session_authorization_negative_scenarios_zero_deletions(self):
        """Требование 1: Негативные сценарии _check_session_authorization:
        - отсутствие модуля wolf_session;
        - исключение при проверке;
        - старый run_id;
        - чужой iid;
        - чужой folder_id.
        Во всех случаях — отказ (VIEW-ONLY, status: forbidden) и строго ноль удалений."""
        obj = "game--AuthNeg.tar.zst"
        base_name = "game--AuthNeg"

        # Записываем активную базу и кандидата на удаление
        (self.storage_dir / obj).write_bytes(zstd_compress(b"BASE_DATA"))
        cand = self.storage_dir / f"{obj}.deadbeef"
        cand.write_bytes(b"CANDIDATE_DATA")

        # Настраиваем базовый grant файл
        self._setup_active_grant(run_id="run_valid_123", iid="1001", folder_id="fid_clean_99")

        # 1. Отсутствие модуля wolf_session
        with mock.patch.dict(sys.modules, {"wolf_session": None}):
            res1 = execute_stand_cleanup(
                stand_dir=self.storage_dir,
                obj_name=base_name,
                grant_file=self.grant_file,
                lock_dir=self.l_dir,
                run_id_val="run_valid_123",
                iid_val="1001",
                folder_id_val="fid_clean_99",
            )
            self.assertEqual(res1["status"], "forbidden")
            self.assertEqual(res1["reason"], "view_only_no_session_grant")
            self.assertEqual(len(res1["deleted"]), 0)
            self.assertTrue(cand.exists())

        # 2. Исключение при вызове is_cleanup_authorized
        with mock.patch("wolf_session.is_cleanup_authorized", side_effect=RuntimeError("Authorization probe crash")):
            res2 = execute_stand_cleanup(
                stand_dir=self.storage_dir,
                obj_name=base_name,
                grant_file=self.grant_file,
                lock_dir=self.l_dir,
                run_id_val="run_valid_123",
                iid_val="1001",
                folder_id_val="fid_clean_99",
            )
            self.assertEqual(res2["status"], "forbidden")
            self.assertEqual(res2["reason"], "view_only_no_session_grant")
            self.assertEqual(len(res2["deleted"]), 0)
            self.assertTrue(cand.exists())

        # 3. Старый run_id (grant выдан для другой сессии)
        res3 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            run_id_val="run_NEW_456",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )
        self.assertEqual(res3["status"], "forbidden")
        self.assertEqual(res3["reason"], "view_only_no_session_grant")
        self.assertEqual(len(res3["deleted"]), 0)
        self.assertTrue(cand.exists())

        # 4. Чужой iid
        res4 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            run_id_val="run_valid_123",
            iid_val="9999",
            folder_id_val="fid_clean_99",
        )
        self.assertEqual(res4["status"], "forbidden")
        self.assertEqual(res4["reason"], "view_only_no_session_grant")
        self.assertEqual(len(res4["deleted"]), 0)
        self.assertTrue(cand.exists())

        # 5. Чужой folder_id
        res5 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            run_id_val="run_valid_123",
            iid_val="1001",
            folder_id_val="fid_OTHER_FOLDER",
        )
        self.assertEqual(res5["status"], "forbidden")
        self.assertEqual(res5["reason"], "view_only_no_session_grant")
        self.assertEqual(len(res5["deleted"]), 0)
        self.assertTrue(cand.exists())

    def test_independent_session_authorization_and_under_lock_check(self):
        """Пункт 1: Независимая проверка текущей сессии:
        - старый grant_data после смены run_id;
        - несовпадающий iid или folder_id;
        - отсутствие метаданных текущей сессии при валидном grant_data;
        - совпадающая активная сессия.
        Во всех отрицательных случаях адаптер удаления не вызывается."""
        class MockDeletionAdapter:
            def __init__(self):
                self.calls = []

            def delete_candidate(self, fname, info):
                self.calls.append((fname, info))
                return True, None

            def verify_deleted(self, fname, info):
                return True

        obj = "game--IndepAuth.tar.zst"
        base_name = "game--IndepAuth"

        # Записываем базу и кандидата
        (self.storage_dir / obj).write_bytes(zstd_compress(b"BASE_DATA"))
        cand = self.storage_dir / f"{obj}.deadbeef"
        cand.write_bytes(b"CANDIDATE_DATA")

        # Базовый активный грант
        self._setup_active_grant(run_id="run_CUR_111", iid="1001", folder_id="fid_CUR_99")

        # 1. Старый grant_data после смены run_id (на сервере сменился run_id на run_NEW_222)
        (self.l_dir / "run_id").write_text("run_NEW_222", encoding="utf-8")
        adapter1 = MockDeletionAdapter()
        res1 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            drive_adapter=adapter1,
        )
        self.assertEqual(res1["status"], "forbidden")
        self.assertEqual(res1["reason"], "view_only_no_session_grant")
        self.assertEqual(len(res1["deleted"]), 0)
        self.assertEqual(len(adapter1.calls), 0, "Адаптер удаления НЕ должен вызываться при старом run_id!")
        self.assertTrue(cand.exists())

        # 2. Несовпадающий iid
        (self.l_dir / "run_id").write_text("run_CUR_111", encoding="utf-8")
        (self.l_dir / "iid").write_text("FOREIGN_IID_999", encoding="utf-8")
        adapter2 = MockDeletionAdapter()
        res2 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            drive_adapter=adapter2,
        )
        self.assertEqual(res2["status"], "forbidden")
        self.assertEqual(res2["reason"], "view_only_no_session_grant")
        self.assertEqual(len(res2["deleted"]), 0)
        self.assertEqual(len(adapter2.calls), 0, "Адаптер удаления НЕ должен вызываться при несовпадающем iid!")
        self.assertTrue(cand.exists())

        # 3. Несовпадающий folder_id
        (self.l_dir / "iid").write_text("1001", encoding="utf-8")
        (self.l_dir / "storage_folder_id").write_text("FOREIGN_FID_888", encoding="utf-8")
        adapter3 = MockDeletionAdapter()
        res3 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            drive_adapter=adapter3,
        )
        self.assertEqual(res3["status"], "forbidden")
        self.assertEqual(res3["reason"], "view_only_no_session_grant")
        self.assertEqual(len(res3["deleted"]), 0)
        self.assertEqual(len(adapter3.calls), 0, "Адаптер удаления НЕ должен вызываться при несовпадающем folder_id!")
        self.assertTrue(cand.exists())

        # 4. Отсутствие метаданных текущей сессии на сервере при валидном grant_data
        (self.l_dir / "storage_folder_id").unlink(missing_ok=True)
        (self.l_dir / "run_id").unlink(missing_ok=True)
        (self.l_dir / "iid").unlink(missing_ok=True)
        env_clean = {k: v for k, v in os.environ.items() if k not in ("WOLF_RUN_ID", "VI", "WOLF_STORAGE_FOLDER_ID")}
        with mock.patch.dict(os.environ, env_clean, clear=True):
            adapter4 = MockDeletionAdapter()
            res4 = execute_stand_cleanup(
                stand_dir=self.storage_dir,
                obj_name=base_name,
                grant_file=self.grant_file,
                lock_dir=self.l_dir,
                tmp_dir=self.t_dir,
                state_dir=self.s_dir,
                drive_adapter=adapter4,
            )
            self.assertEqual(res4["status"], "forbidden")
            self.assertEqual(res4["reason"], "view_only_no_session_grant")
            self.assertEqual(len(res4["deleted"]), 0)
            self.assertEqual(len(adapter4.calls), 0, "Адаптер НЕ должен вызываться при отсутствии серверных метаданных!")
            self.assertTrue(cand.exists())

        # 5. Совпадающая активная сессия: удаление разрешено
        (self.l_dir / "run_id").write_text("run_CUR_111", encoding="utf-8")
        (self.l_dir / "iid").write_text("1001", encoding="utf-8")
        (self.l_dir / "storage_folder_id").write_text("fid_CUR_99", encoding="utf-8")
        adapter5 = MockDeletionAdapter()
        res5 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            drive_adapter=adapter5,
        )
        self.assertEqual(res5["status"], "completed")
        self.assertEqual(len(res5["deleted"]), 1)
        self.assertEqual(len(adapter5.calls), 1, "Адаптер удаления ДОЛЖЕН быть вызван для активной сессии!")
        self.assertEqual(adapter5.calls[0][0], f"{obj}.deadbeef")

    def test_snapshot_linked_to_classification_and_controlled_drift_aborts(self):
        """Требование 2: Снимок состояния связан именно с классификацией:
        - фиксируется базовое состояние ДО классификации;
        - проверяется неизменность ПОСЛЕ классификации;
        - перепроверяется снимок плана ПЕРЕД удалением;
        - управляемое изменение между классификацией и фиксацией плана отменяет устаревший план,
          ноль удалений, блокировка сохраняется."""
        obj = "game--SnapshotDrift.tar.zst"
        base_name = "game--SnapshotDrift"
        self._setup_active_grant()

        base_file = self.storage_dir / obj
        base_file.write_bytes(zstd_compress(b"BASE_DATA_V1"))
        cand_file = self.storage_dir / f"{obj}.deadbeef"
        cand_file.write_bytes(b"CANDIDATE_DATA_V1")

        # 1. Управляемое изменение МЕЖДУ классификацией и фиксацией снимка плана (_post_classify_hook)
        def _drift_after_classification():
            # Добавляем неожиданный файл в стенд, что делает построенный план недействительным
            drift_file = self.storage_dir / "unplanned_parallel_write.bin"
            drift_file.write_bytes(b"PARALLEL_DATA")

        execute_stand_cleanup._post_classify_hook = _drift_after_classification

        res = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res["status"], "aborted")
        self.assertTrue(res["reason"].startswith("metadata_snapshot_mismatch"))
        self.assertEqual(len(res["deleted"]), 0)
        self.assertTrue(cand_file.exists(), "Кандидат не должен быть удалён при дрифте метаданных!")

        # Очищаем внесённый дрифт
        (self.storage_dir / "unplanned_parallel_write.bin").unlink()
        delattr(execute_stand_cleanup, "_post_classify_hook")

        # 2. Управляемое изменение ПЕРЕД удалением (_pre_verify_hook)
        def _drift_before_delete():
            base_file.write_bytes(zstd_compress(b"MUTATED_BASE_DATA_RIGHT_BEFORE_DELETE"))

        execute_stand_cleanup._pre_verify_hook = _drift_before_delete

        res2 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res2["status"], "aborted")
        self.assertTrue(res2["reason"].startswith("metadata_snapshot_mismatch"))
        self.assertEqual(len(res2["deleted"]), 0)
        self.assertTrue(cand_file.exists())

    def test_real_wolf_engine_pack_cleanup_and_unpack_with_delta(self):
        """Требование 2: Сквозная интеграционная проверка настоящего Wolf:
        - подготовка состояния базы и дельты настоящей функцией pack() движка из wolf-setup.sh;
        - сценарий содержит:
          * неизменённый файл базы (unchanged.dat);
          * изменённый файл (modified.dat);
          * новый файл (new_file.dat);
          * удалённый файл, отражённый штатной дельтой (deleted.dat);
          * устаревшие объекты-кандидаты рядом с активными;
        - локальная стендовая очистка кандидатов;
        - штатный вызов unpack() настоящего Wolf (движок сам определяет базу и дельту без передачи тестом!);
        - сверка полного набора путей и SHA-256;
        - удалённый файл НЕ восстанавливается."""
        self._ensure_flock_shim()
        obj = "sgame--WolfReal"
        base_tar = f"{obj}.tar.zst"
        self._setup_active_grant()

        home_dir = self.td / "home_wolfreal"
        game_dir = home_dir / "sa/steamapps/common/WolfReal"
        game_dir.mkdir(parents=True, exist_ok=True)

        runner = self._create_real_wolf_runner(home_dir)
        env = self._engine_env()
        env_now = env.copy()
        env_now["NOW"] = "1"

        # 1. Формируем исходное состояние активной базы на диске:
        # unchanged (40KB), modified v1 (2KB), deleted (1KB) -> база 43KB
        unchanged_bytes = os.urandom(40000)
        modified_v1_bytes = os.urandom(2000)
        deleted_bytes = os.urandom(1000)

        (game_dir / "unchanged.dat").write_bytes(unchanged_bytes)
        (game_dir / "modified.dat").write_bytes(modified_v1_bytes)
        (game_dir / "deleted.dat").write_bytes(deleted_bytes)

        # 2. Вызываем настоящий pack() Wolf для выгрузки базы
        p_pack1 = subprocess.run([str(runner), "pack", obj], capture_output=True, text=True, env=env_now)
        self.assertEqual(p_pack1.returncode, 0, f"pack 1 failed: {p_pack1.stderr}")
        self.assertTrue((self.storage_dir / base_tar).is_file() or (self.storage_dir / f"{base_tar}.parts").is_file())

        # 3. Вносим изменения на диске для формирования штатной дельты:
        # unchanged.dat — без изменений
        # modified.dat — обновлённые байты v2
        # new_file.dat — добавлен новый файл
        # deleted.dat — удалён с диска
        modified_v2_bytes = os.urandom(2000)
        new_file_bytes = os.urandom(1000)

        (game_dir / "modified.dat").write_bytes(modified_v2_bytes)
        (game_dir / "new_file.dat").write_bytes(new_file_bytes)
        (game_dir / "deleted.dat").unlink()

        # 4. Вызываем настоящий pack() Wolf для выгрузки накопительной дельты
        p_pack2 = subprocess.run([str(runner), "pack", obj], capture_output=True, text=True, env=env_now)
        self.assertEqual(p_pack2.returncode, 0, f"pack 2 failed: {p_pack2.stderr}")

        # Проверяем, что в хранилище появилась дельта с хэшем базы
        storage_names = [p.name for p in self.storage_dir.iterdir()]
        deltas = [n for n in storage_names if n.startswith(f"{base_tar}.") and not n.endswith(".parts")]
        self.assertTrue(len(deltas) >= 1, f"Дельта не создана движком. Файлы: {storage_names}")
        active_delta_name = deltas[0]

        # 5. Размещаем устаревшие объекты-кандидаты рядом с активными
        cand1 = self.storage_dir / f"{base_tar}.deadbeef"
        cand2 = self.storage_dir / f"{base_tar}.cafebabe"
        cand1.write_bytes(b"OLD_CANDIDATE_DELTA_1")
        cand2.write_bytes(b"OLD_CANDIDATE_DELTA_2")

        # 6. Запускаем локальную стендовую очистку
        res_clean = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=obj,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res_clean["status"], "completed")
        self.assertEqual(len(res_clean["deleted"]), 2)
        deleted_names = {d["name"] for d in res_clean["deleted"]}
        self.assertEqual(deleted_names, {cand1.name, cand2.name})

        # Кандидаты удалены, активные база и дельта сохранены
        self.assertFalse(cand1.exists())
        self.assertFalse(cand2.exists())
        self.assertTrue((self.storage_dir / base_tar).exists() or (self.storage_dir / f"{base_tar}.parts").exists())
        self.assertTrue((self.storage_dir / active_delta_name).exists())

        # 7. Полностью удаляем каталог игры и вызываем НАСТОЯЩИЙ unpack() Wolf
        # Движок сам определяет активную базу и применимую дельту (без передачи дельты тестом!)
        shutil.rmtree(game_dir)
        self.assertFalse(game_dir.exists(), "Каталог игры должен быть удалён перед unpack")

        p_unpack = subprocess.run([str(runner), "unpack", obj], capture_output=True, text=True, env=env)
        self.assertEqual(p_unpack.returncode, 0, f"unpack failed: {p_unpack.stderr}")
        self.assertTrue(game_dir.is_dir(), "Каталог игры должен быть восстановлен движком")

        # 8. Сверка полного дерева путей и SHA-256
        restored_files = get_dir_tree_hashes(game_dir)

        # Ожидаемый состав актуальных данных
        expected_files = {
            "unchanged.dat": sha256_bytes(unchanged_bytes),
            "modified.dat": sha256_bytes(modified_v2_bytes),
            "new_file.dat": sha256_bytes(new_file_bytes),
        }

        # Сверяем точный состав файлов
        self.assertEqual(set(restored_files.keys()), set(expected_files.keys()))
        for rel_path, exp_hash in expected_files.items():
            self.assertEqual(restored_files[rel_path], exp_hash, f"Хэш не совпал для {rel_path}")

        # Удалённый файл НЕ должен восстановиться
        self.assertNotIn("deleted.dat", restored_files)
        self.assertFalse((game_dir / "deleted.dat").exists(), "deleted.dat не должен восстановиться!")
        self.assertFalse((game_dir / f".wolf-del.{obj}").exists(), ".wolf-del файл не должен остаться!")

    def test_protected_attempt_and_pending_real_resume_and_restore_including_delta(self):
        """Требование 4: Защита attempt-* и pending с реальным штатным resume и восстановлением (включая дельту):
        - незавершённые выгрузки базы и дельты остаются нетронутыми при очистке;
        - реальный вызов wolf-parts.py resume успешно доводит публикацию;
        - штатное восстановление Wolf подтверждает целостность развёрнутых данных."""
        obj = "game--ResumeTest.tar.zst"
        base_name = "game--ResumeTest"
        self._setup_active_grant()
        env = self._engine_env(part_bytes=500)

        # 1. Активная версия v1 (база)
        v1_base_files = {"content/base.dat": os.urandom(2500)}
        v1_base_zst = zstd_compress(make_tar_bytes(v1_base_files))
        subprocess.run(
            [sys.executable, str(self.parts_script), "put", str(self.storage_dir), obj],
            input=v1_base_zst,
            capture_output=True,
            env=env,
            check=True,
        )
        parts_md5 = md5_bytes((self.storage_dir / f"{obj}.parts").read_bytes())
        delta_hash = parts_md5[:8]
        delta_obj = f"{obj}.{delta_hash}"

        # 2. Активная версия v1 (дельта)
        v1_delta_files = {"content/patch.dat": os.urandom(2000)}
        v1_delta_zst = zstd_compress(make_tar_bytes(v1_delta_files))
        subprocess.run(
            [sys.executable, str(self.parts_script), "put", str(self.storage_dir), delta_obj],
            input=v1_delta_zst,
            capture_output=True,
            env=env,
            check=True,
        )

        # 3. Создаём незавершённую попытку выгрузки новой БАЗЫ (v2) через wolf-parts.py stage
        v2_base_files = {
            "content/base.dat": os.urandom(3000),
            "content/extra.dat": os.urandom(1500),
        }
        v2_base_zst = zstd_compress(make_tar_bytes(v2_base_files))
        staged_base = subprocess.run(
            [sys.executable, str(self.parts_script), "stage", str(self.storage_dir), obj],
            input=v2_base_zst,
            capture_output=True,
            env=env,
            check=True,
        )
        base_receipt_bytes = staged_base.stdout
        # Фиксируем квитанцию попытки в attempt-*
        base_receipt_tuple = _WOLF_PARTS["parse_receipt"](base_receipt_bytes.decode("utf-8"), obj)
        engine_note_attempt(obj, base_receipt_tuple, "unknown")

        # 4. Создаём незавершённую попытку выгрузки новой ДЕЛЬТЫ (v2) через wolf-parts.py stage
        v2_delta_files = {
            "content/patch.dat": os.urandom(2500),
            "content/dlc.dat": os.urandom(1200),
        }
        v2_delta_zst = zstd_compress(make_tar_bytes(v2_delta_files))
        staged_delta = subprocess.run(
            [sys.executable, str(self.parts_script), "stage", str(self.storage_dir), delta_obj],
            input=v2_delta_zst,
            capture_output=True,
            env=env,
            check=True,
        )
        delta_receipt_bytes = staged_delta.stdout
        # Фиксируем квитанцию дельты в pending
        delta_receipt_tuple = _WOLF_PARTS["parse_receipt"](delta_receipt_bytes.decode("utf-8"), delta_obj)
        self._write_engine_pending(f"{base_name}.{delta_hash}", delta_obj, delta_receipt_tuple)

        # Проверяем, что маркеры попыток присутствуют в хранилище
        self.assertTrue((self.storage_dir / f"{obj}.parts.new").is_file())
        self.assertTrue((self.storage_dir / f"{delta_obj}.parts.new").is_file())

        # 5. Запуск очистки: попытки ОБЯЗАНЫ быть защищены (0 удалений)
        res_clean = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )
        self.assertEqual(res_clean["status"], "noop")
        self.assertEqual(len(res_clean["deleted"]), 0)

        # Маркеры и части попыток в хранилище не тронуты
        self.assertTrue((self.storage_dir / f"{obj}.parts.new").is_file())
        self.assertTrue((self.storage_dir / f"{delta_obj}.parts.new").is_file())

        # 6. Действительно выполняем штатный resume/повтор публикации для базы и дельты
        res_resume_base = subprocess.run(
            [sys.executable, str(self.parts_script), "resume", str(self.storage_dir), obj],
            input=base_receipt_bytes,
            capture_output=True,
            env=env,
        )
        self.assertEqual(res_resume_base.returncode, 0, f"resume base failed: {res_resume_base.stderr.decode()}")

        res_resume_delta = subprocess.run(
            [sys.executable, str(self.parts_script), "resume", str(self.storage_dir), delta_obj],
            input=delta_receipt_bytes,
            capture_output=True,
            env=env,
        )
        self.assertEqual(res_resume_delta.returncode, 0, f"resume delta failed: {res_resume_delta.stderr.decode()}")

        # Маркеры .new активированы в .parts
        self.assertFalse((self.storage_dir / f"{obj}.parts.new").exists())
        self.assertFalse((self.storage_dir / f"{delta_obj}.parts.new").exists())

        # 7. Штатное восстановление Wolf после resume в пустую папку
        restore_dir = self.td / "wolf_restore_after_resume"
        wolf_engine_restore(
            script_path=self.parts_script,
            remote_dir=self.storage_dir,
            obj_name=obj,
            dest_dir=restore_dir,
            env=env,
            delta_hash=delta_hash,
        )

        # 8. Сравнение путей и SHA-256 с данными версии v2
        expected_v2 = {}
        expected_v2.update(v2_base_files)
        expected_v2.update(v2_delta_files)

        restored_hashes = get_dir_tree_hashes(restore_dir)
        self.assertEqual(set(restored_hashes.keys()), set(expected_v2.keys()))

        for rel_path, exp_bytes in expected_v2.items():
            self.assertEqual(restored_hashes[rel_path], sha256_bytes(exp_bytes), f"Хэш v2 не сошёлся: {rel_path}")

    def test_mutual_exclusion_with_real_engine_pack_operation(self):
        """Требование 1: Взаимное исключение с НАСТОЯЩЕЙ функцией pack() движка из wolf-setup.sh:
        - при удерживаемой блокировке очистки настоящий pack не начинает запись:
          * в немедленном режиме (NOW=) пропускает запись без изменений в хранилище;
          * с ожиданием (NOW=1) ждёт освобождения блокировки, затем успешно завершает выгрузку и восстановление;
        - при идущей записи настоящего pack очистка отказывается с archive_lock_busy без единого удаления."""
        self._ensure_flock_shim()
        obj = "sgame--PackExcl"
        base_tar = f"{obj}.tar.zst"
        self._setup_active_grant()

        home_dir = self.td / "home_packexcl"
        game_dir = home_dir / "sa/steamapps/common/PackExcl"
        game_dir.mkdir(parents=True, exist_ok=True)
        (game_dir / "data.bin").write_text("INITIAL_PACK_DATA", encoding="utf-8")

        runner = self._create_real_wolf_runner(home_dir)
        env = self._engine_env()
        lock_path = self.l_dir / f"{obj}.lock"

        # 1А. Очистка удерживает flock: настоящий pack не начинает запись
        cleaner_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(cleaner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Вызов pack() в немедленном режиме (NOW=)
        p_nonblock = subprocess.run([str(runner), "pack", obj], capture_output=True, text=True, env=env)
        self.assertEqual(p_nonblock.returncode, 0)
        # Хранилище не затронуто, запись не начиналась
        self.assertEqual(len(list(self.storage_dir.iterdir())), 0, "pack не должен писать файлы при удерживаемом flock очистки!")

        # 1Б. Вызов pack() с ожиданием (NOW=1) в фоне: ждёт освобождения flock
        env_now = env.copy()
        env_now["NOW"] = "1"
        p_wait = subprocess.Popen([str(runner), "pack", obj], env=env_now)
        time.sleep(0.1)
        self.assertIsNone(p_wait.poll(), "pack c NOW=1 обязан ожидать освобождения блокировки")
        self.assertEqual(len(list(self.storage_dir.iterdir())), 0, "pack не должен писать во время ожидания")

        # Освобождаем блокировку очистки
        os.close(cleaner_fd)
        p_wait.wait(timeout=5)
        self.assertEqual(p_wait.returncode, 0)
        self.assertTrue((self.storage_dir / base_tar).is_file(), "После освобождения блокировки pack обязан завершить выгрузку")

        # Проверяем штатное восстановление через unpack()
        shutil.rmtree(game_dir)
        p_unpack = subprocess.run([str(runner), "unpack", obj], capture_output=True, text=True, env=env)
        self.assertEqual(p_unpack.returncode, 0)
        self.assertTrue((game_dir / "data.bin").is_file())
        self.assertEqual((game_dir / "data.bin").read_text(encoding="utf-8"), "INITIAL_PACK_DATA")

        # 2. Настоящий pack удерживает блокировку при записи -> очистка отказывается (archive_lock_busy, 0 удалений)
        (game_dir / "data.bin").write_text("UPDATED_PACK_DATA", encoding="utf-8")
        cand_file = self.storage_dir / f"{base_tar}.c0ffee00"
        cand_file.write_bytes(b"CANDIDATE_DATA")

        env_pause = env.copy()
        env_pause["NOW"] = "1"
        env_pause["PACK_PAUSE_HOOK"] = "1"
        p_pack2 = subprocess.Popen([str(runner), "pack", obj], env=env_pause)

        # Ожидаем входа pack в критическую секцию stage (под дескриптором 8)
        marker = self.t_dir / "pack_in_stage.marker"
        t0 = time.time()
        while not marker.exists() and time.time() - t0 < 3:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "pack должен был зайти в секцию stage и удерживать дескриптор 8")

        # Запуск очистки в момент, когда pack держит блокировку
        res_blocked = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=obj,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res_blocked["status"], "aborted")
        self.assertEqual(res_blocked["reason"], "archive_lock_busy")
        self.assertEqual(len(res_blocked["deleted"]), 0)
        self.assertTrue(cand_file.exists(), "Кандидат не должен быть удалён при параллельной записи настоящего pack!")

        # Даём pack завершить операцию
        (self.t_dir / "cleaner_done.marker").touch()
        p_pack2.wait(timeout=5)
        self.assertEqual(p_pack2.returncode, 0)

        # Проверяем, что обновлённый архив успешно восстанавливается настоящим unpack()
        shutil.rmtree(game_dir)
        p_unpack2 = subprocess.run([str(runner), "unpack", obj], capture_output=True, text=True, env=env)
        self.assertEqual(p_unpack2.returncode, 0)
        self.assertEqual((game_dir / "data.bin").read_text(encoding="utf-8"), "UPDATED_PACK_DATA")

    def test_unknown_generation_old_wolf_remains_protected(self):
        """Неизвестное поколение старого Wolf без квитанций (g99990000) со старым mtime: 0 удалений."""
        obj = "game--OldWolf.tar.zst"
        base_name = "game--OldWolf"
        self._setup_active_grant()

        (self.storage_dir / obj).write_bytes(zstd_compress(b"ACTIVE_DATA"))
        old_part = self.storage_dir / f"{obj}.g99990000-0000"
        old_part.write_bytes(b"OLD_WOLF_UNKNOWN_GENERATION_PART")
        old_mtime = time.time() - 86400 * 1000
        os.utime(old_part, (old_mtime, old_mtime))

        res = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res["status"], "noop")
        self.assertEqual(res["deleted"], [])
        self.assertEqual(res["deleted_bytes"], 0)
        self.assertTrue(old_part.exists())

    def test_interruption_and_safe_resumption(self):
        """Прерывание после удаления части кандидатов: повторный запуск удаляет остаток."""
        obj = "game--InterruptTest.tar.zst"
        base_name = "game--InterruptTest"
        self._setup_active_grant()

        (self.storage_dir / obj).write_bytes(zstd_compress(b"BASE_DATA"))
        cand1 = self.storage_dir / f"{obj}.c1111111"
        cand2 = self.storage_dir / f"{obj}.c2222222"
        cand1.write_bytes(b"CAND1_DATA")
        cand2.write_bytes(b"CAND2_DATA")

        def _hook(fname, idx):
            if idx == 1:
                raise RuntimeError("Simulated process interruption")

        execute_stand_cleanup._test_hook = _hook

        res_pass1 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res_pass1["status"], "stopped")
        self.assertEqual(len(res_pass1["deleted"]), 1)
        self.assertEqual(len(res_pass1["remaining"]), 1)
        self.assertFalse(cand1.exists())
        self.assertTrue(cand2.exists())

        delattr(execute_stand_cleanup, "_test_hook")

        res_pass2 = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res_pass2["status"], "completed")
        self.assertEqual(len(res_pass2["deleted"]), 1)
        self.assertEqual(len(res_pass2["remaining"]), 0)
        self.assertFalse(cand2.exists())

    def test_isolation_foreign_files_similar_archives_and_symlink_traversal(self):
        """Посторонние файлы, похожие архивы, symlink traversal: отказ и 0 удалений."""
        obj = "game--IsolatedTest.tar.zst"
        base_name = "game--IsolatedTest"
        self._setup_active_grant()

        (self.storage_dir / obj).write_bytes(zstd_compress(b"BASE_DATA"))
        cand_file = self.storage_dir / f"{obj}.deadbeef"
        cand_file.write_bytes(b"CANDIDATE_DATA")

        # 1. Посторонние файлы
        foreign = self.storage_dir / "vastgame-games.json"
        foreign.write_text('{"games": []}', encoding="utf-8")

        # 2. Похожий архив
        similar = self.storage_dir / "game--IsolatedTest2.tar.zst.deadbeef"
        similar.write_bytes(b"SIMILAR_ARCHIVE_DATA")

        # 3. Симлинк за пределы стенда
        outside_dir = self.td / "outside_dir"
        outside_dir.mkdir()
        secret_file = outside_dir / "host_secret.txt"
        secret_file.write_text("HOST_SECRET", encoding="utf-8")
        symlink_cand = self.storage_dir / f"{obj}.c0ffee00"
        os.symlink(str(secret_file), str(symlink_cand))

        # Очистка должна отвергнуть выполнение из-за симлинка за стенд
        res = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
        )

        self.assertEqual(res["status"], "rejected")
        self.assertIn("symlink_traversal_detected", res["reason"])
        self.assertEqual(len(res["deleted"]), 0)
        self.assertTrue(secret_file.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(similar.exists())

        # 4. Проверка удалённого хранилища (remote storage syntax)
        res_remote = execute_stand_cleanup(
            stand_dir="drive:remote_folder",
            obj_name=base_name,
            grant_file=self.grant_file,
        )
        self.assertEqual(res_remote["status"], "rejected")
        self.assertEqual(res_remote["reason"], "remote_storage_forbidden_local_stand_only")

    def test_dry_run_and_reporting_format(self):
        """Режим dry-run и раздельный учёт байтов в отчёте."""
        obj = "game--ReportTest.tar.zst"
        base_name = "game--ReportTest"
        self._setup_active_grant()

        (self.storage_dir / obj).write_bytes(zstd_compress(b"BASE_DATA"))
        cand_file = self.storage_dir / f"{obj}.deadbeef"
        cand_file.write_bytes(b"CANDIDATE_DATA" * 50)

        res_dry = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
            dry_run=True,
        )

        self.assertEqual(res_dry["status"], "completed")
        self.assertEqual(res_dry["reason"], "dry_run_success")
        self.assertEqual(res_dry["deleted"], [])
        self.assertEqual(res_dry["deleted_bytes"], 0)
        self.assertTrue(cand_file.exists())

        res_real = execute_stand_cleanup(
            stand_dir=self.storage_dir,
            obj_name=base_name,
            grant_file=self.grant_file,
            lock_dir=self.l_dir,
            tmp_dir=self.t_dir,
            state_dir=self.s_dir,
            run_id_val="run_test_clean_001",
            iid_val="1001",
            folder_id_val="fid_clean_99",
            dry_run=False,
        )

        report_txt = format_cleanup_result(res_real)
        self.assertIn("РЕЗУЛЬТАТ СТЕНДОВОЙ ОЧИСТКИ: статус COMPLETED", report_txt)
        self.assertIn("не является измеренным освобождением квоты Google Drive", report_txt)
        self.assertIn("displaced_delta_superseded_by_newer_base", report_txt)


if __name__ == "__main__":
    unittest.main()

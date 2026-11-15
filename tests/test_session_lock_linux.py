# tests/test_session_lock_linux.py — тесты блокировок и жизненного цикла сессии в Linux.
#
# Проверяет:
# 1. Долгоживущий координатор вызывает session_start и остаётся работать (fd 8 не утекает, session.lock свободен).
# 2. Отдельный процесс успешно выдаёт разрешение grant-cleanup для этой сессии под flock.
# 3. Второй координатор не может запуститься параллельно (supervise.lock занят).
# 4. После завершения первого координатора новая сессия запускается и аннулирует старое разрешение.
# 5. При занятом session.lock попытка session_start завершается отказом, не меняя run_id и не удаляя grant.
# 6. grant_cleanup освобождает session.lock на каждом пути выхода (ошибочном и успешном).
# 7. Общее время серверного определения folder_id ограничено (timeout/bounded retries).
# 8. Обычная остановка гарантированно останавливает всех работников и дерево процессов.
# 9. Потомок в дереве процессов задерживает освобождение блокировки до полного завершения.
# 10. При неподтверждённых работниках создаётся supervise.failed, блокирующий последующие запуски.
# 11. Гонка: предварительная проверка пройдена, но под блокировкой зафиксирован supervise.failed -> строгий отказ.
# 12. Ошибка записи аварийного маркера удерживает блокировку и не даёт запуститься новому координатору.

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestSessionLockLinux(unittest.TestCase):
    """Тесты блокировок и жизненного цикла сессии с настоящим Linux flock."""

    @classmethod
    def setUpClass(cls):
        # Если тест запущен на macOS без flock, но есть Docker — перезапускаем в Docker
        if sys.platform != "linux" or not shutil.which("flock"):
            if shutil.which("docker"):
                cls.run_via_docker = True
            else:
                raise unittest.SkipTest("Требуется Linux с flock или Docker")
        else:
            cls.run_via_docker = False

    def setUp(self):
        if getattr(self, "run_via_docker", False):
            return

        self.td = Path(tempfile.mkdtemp())
        self.l_dir = self.td / "run_wolf"
        self.l_dir.mkdir(parents=True, exist_ok=True)
        self.s_dir = self.td / "var_lib_wolf"
        self.s_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = self.td / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.td / "wolf.log"
        self.running_pids = []

        # Mock rclone
        self.fake_rclone = self.bin_dir / "rclone"
        self.fake_rclone.write_text(
            "#!/bin/sh\n"
            "if [ \"${HANG_RCLONE:-0}\" = \"1\" ]; then\n"
            "  sleep 30\n"
            "  exit 1\n"
            "fi\n"
            "if [ \"${FAIL_RCLONE:-0}\" = \"1\" ]; then\n"
            "  echo 'rclone error' >&2\n"
            "  exit 1\n"
            "fi\n"
            "case \"$*\" in\n"
            "  *\"lsjson -M --stat \"*)\n"
            "    echo '{\"ID\": \"drive_fid_42\", \"Name\": \"vastgame-test\", \"IsDir\": true}'\n"
            "    exit 0\n"
            "    ;;\n"
            "  *\"lsjson -M --dirs-only \"*)\n"
            "    echo '[{\"ID\": \"drive_fid_42\", \"Name\": \"vastgame-test\", \"IsDir\": true}]'\n"
            "    exit 0\n"
            "    ;;\n"
            "  *)\n"
            "    exit 0\n"
            "    ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        self.fake_rclone.chmod(0o755)

        # Mock wolf-web.py
        self.fake_web = self.bin_dir / "fake_web.py"
        self.fake_web.write_text(
            "#!/usr/bin/env python3\n"
            "import signal, sys, time\n"
            "def on_term(s, f):\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, on_term)\n"
            "while True:\n"
            "    time.sleep(0.05)\n",
            encoding="utf-8",
        )
        self.fake_web.chmod(0o755)

        # Извлекаем функции из wolf-setup.sh
        src = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")
        fn_rc_call = re.search(r"^_rc_stat_call\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)
        fn_storage = re.search(r"^storage_id\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)
        fn_session = re.search(r"^session_start\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)
        fn_grant = re.search(r"^grant_cleanup\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)
        fn_supervise = re.search(r"^supervise\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)

        self.wolf_sh = self.td / "wolf"
        self.wolf_sh.write_text(
            f"#!/bin/bash\n"
            f"set -e\n"
            f"export PATH=\"{str(self.bin_dir)}:$PATH\"\n"
            f"export WOLF_WEB_PY=\"{str(self.fake_web)}\"\n"
            f"L={str(self.l_dir)!r}\n"
            f"S={str(self.s_dir)!r}\n"
            f"VI=\"1001\"\n"
            f"R=\"vastgame:vastgame-test\"\n"
            f"log() {{ echo \"[$(date '+%F %T')] $*\" >> {str(self.log_file)!r}; }}\n"
            f"low() {{ exec \"$@\"; }}\n"
            f"agent_mode() {{ echo \"off\"; }}\n"
            f"agent_stop() {{ :; }}\n"
            f"optin_enabled() {{ return 1; }}\n"
            f"{fn_rc_call}\n"
            f"{fn_storage}\n"
            f"{fn_session}\n"
            f"{fn_grant}\n"
            f"{fn_supervise}\n"
            f"case ${{1:-}} in\n"
            f"  session-start) session_start ;;\n"
            f"  grant-cleanup) shift; grant_cleanup \"$@\" ;;\n"
            f"  storage-id) storage_id ;;\n"
            f"  supervise) supervise ;;\n"
            f"  watch)\n"
            f"    if [ \"${{DELAY_WORKER_WATCH:-0}}\" = \"1\" ]; then\n"
            f"      trap 'sleep 2; exit 0' TERM INT\n"
            f"      while :; do sleep 0.05 & wait $!; done\n"
            f"    elif [ \"${{TREE_WORKER_WATCH:-0}}\" = \"1\" ]; then\n"
            f"      (\n"
            f"        trap 'sleep 1.5; exit 0' TERM INT\n"
            f"        while :; do sleep 0.05 & wait $!; done\n"
            f"      ) &\n"
            f"      echo \"$!\" > \"$L/child_writer.pid\"\n"
            f"      exit 0\n"
            f"    else\n"
            f"      trap 'exit 0' TERM INT\n"
            f"      while :; do sleep 0.05 & wait $!; done\n"
            f"    fi\n"
            f"    ;;\n"
            f"  boot)\n"
            f"    : > \"$L/boot-done\"\n"
            f"    trap 'exit 0' TERM INT\n"
            f"    while :; do sleep 0.05 & wait $!; done\n"
            f"    ;;\n"
            f"esac\n",
            encoding="utf-8",
        )
        self.wolf_sh.chmod(0o755)

    def tearDown(self):
        if getattr(self, "run_via_docker", False):
            return

        # Гарантированное завершение всех порождённых процессов
        for pid in self.running_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(0.1)
        for pid in self.running_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(self.td, ignore_errors=True)

    def _run_in_docker_if_needed(self, test_name):
        if getattr(self, "run_via_docker", False):
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{str(ROOT)}:/app",
                "-w", "/app",
                "python:3.12-slim",
                "python3", "-m", "unittest", f"tests.test_session_lock_linux.TestSessionLockLinux.{test_name}",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            self.assertEqual(res.returncode, 0, f"Docker test {test_name} failed:\n{res.stdout}\n{res.stderr}")
            return True
        return False

    def _assert_process_terminated(self, pid, msg=None):
        """Проверяет, что процесс завершён (не существует или переведён в zombie)."""
        try:
            while True:
                r_pid, _ = os.waitpid(-1, os.WNOHANG)
                if r_pid <= 0:
                    break
        except (ChildProcessError, OSError):
            pass

        try:
            os.kill(pid, 0)
            stat_f = Path(f"/proc/{pid}/stat")
            if stat_f.exists():
                try:
                    content = stat_f.read_text().split(")")[-1].split()
                    if content and content[0] == "Z":
                        return
                except Exception:
                    pass
            self.fail(msg or f"Процесс {pid} всё ещё выполняется")
        except ProcessLookupError:
            pass

    def test_real_sequence_supervise_and_grant_cleanup(self):
        """Реальная последовательность жизненного цикла координатора и выдачи разрешения."""
        if self._run_in_docker_if_needed("test_real_sequence_supervise_and_grant_cleanup"):
            return

        # 1. Долгоживущий координатор 1 вызывает session_start и остаётся работать в фоне
        p1 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.running_pids.append(p1.pid)

        # Ждем старта сессии (run_id записан)
        run_id_file = self.l_dir / "run_id"
        t0 = time.time()
        while not run_id_file.exists() and time.time() - t0 < 5.0:
            time.sleep(0.05)
        self.assertTrue(run_id_file.exists(), "run_id не был создан координатором")
        run_id_1 = run_id_file.read_text().strip()
        self.assertTrue(len(run_id_1) >= 16)

        # Проверка: координатор держит supervise.lock
        res_sup_lock = subprocess.run(
            ["flock", "-n", str(self.l_dir / "supervise.lock"), "echo", "held"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(res_sup_lock.returncode, 0, "supervise.lock должен быть занят координатором 1")

        # Проверка: session.lock НЕ заблокирован координатором (fd 8 закрыт)
        res_sess_lock = subprocess.run(
            ["flock", "-n", str(self.l_dir / "session.lock"), "echo", "free"],
            capture_output=True, text=True,
        )
        self.assertEqual(res_sess_lock.returncode, 0, "session.lock должен быть свободен")

        # 2. Отдельный процесс успешно выдаёт разрешение для этой сессии
        res_grant = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id_1, "drive_fid_42"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(res_grant.returncode, 0, f"grant-cleanup завершился ошибкой: {res_grant.stderr}")
        grant_file = self.l_dir / "cleanup_grant.json"
        self.assertTrue(grant_file.exists())
        grant_data = json.loads(grant_file.read_text(encoding="utf-8"))
        self.assertEqual(grant_data["status"], "granted")
        self.assertEqual(grant_data["run_id"], run_id_1)

        # 3. Второй координатор пытается запуститься параллельно -> строгий отказ
        p2 = subprocess.run(
            ["bash", str(self.wolf_sh), "supervise"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(p2.returncode, 0, "Второй координатор не должен был запуститься")
        # Состояние сессии 1 не повреждено
        self.assertEqual(run_id_file.read_text().strip(), run_id_1)
        self.assertTrue(grant_file.exists())

        # 4. Корректное завершение первого координатора
        p1.terminate()
        p1.wait(timeout=5)
        self.running_pids.remove(p1.pid)

        # supervise.lock освободился
        res_sup_free = subprocess.run(
            ["flock", "-n", str(self.l_dir / "supervise.lock"), "echo", "free"],
            capture_output=True, text=True,
        )
        self.assertEqual(res_sup_free.returncode, 0, "supervise.lock должен освободиться после остановки")

        # 5. Новая сессия запускается и отвергает старое разрешение
        p3 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.running_pids.append(p3.pid)

        t0 = time.time()
        while time.time() - t0 < 5.0:
            if run_id_file.exists() and run_id_file.read_text().strip() != run_id_1:
                break
            time.sleep(0.05)
        run_id_2 = run_id_file.read_text().strip()
        self.assertNotEqual(run_id_1, run_id_2, "Новая сессия должна получить новый run_id")

        # Старое разрешение аннулировано (файл удалён)
        self.assertFalse(grant_file.exists(), "Старый файл разрешения должен быть удален при старте новой сессии")

        # Остановка координатора 3
        p3.terminate()
        p3.wait(timeout=5)
        self.running_pids.remove(p3.pid)

    def test_busy_session_lock_fails_session_start_without_damaging_state(self):
        """При занятом session.lock попытка session_start завершается отказом, не меняя run_id и grant."""
        if self._run_in_docker_if_needed("test_busy_session_lock_fails_session_start_without_damaging_state"):
            return

        # Исходное состояние сессии
        run_id_file = self.l_dir / "run_id"
        run_id_file.write_text("orig_run_id_111\n", encoding="utf-8")
        grant_file = self.l_dir / "cleanup_grant.json"
        grant_file.write_text('{"status": "granted", "run_id": "orig_run_id_111"}', encoding="utf-8")

        # Захватываем session.lock в отдельном процессе
        lock_holder = subprocess.Popen(
            ["flock", "-x", str(self.l_dir / "session.lock"), "sleep", "10"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.running_pids.append(lock_holder.pid)
        time.sleep(0.2)

        env_fast = dict(os.environ, WOLF_LOCK_WAIT="1")

        # Попытка session_start с таймаутом ожидания
        res_start = subprocess.run(
            ["bash", str(self.wolf_sh), "session-start"],
            capture_output=True, text=True, timeout=15, env=env_fast,
        )
        # Обязан завершиться с кодом ошибки
        self.assertNotEqual(res_start.returncode, 0)

        # Состояние сессии НЕ повреждено
        self.assertEqual(run_id_file.read_text().strip(), "orig_run_id_111")
        self.assertTrue(grant_file.exists())
        self.assertEqual(json.loads(grant_file.read_text())["run_id"], "orig_run_id_111")

        # Попытка supervise также должна завершиться отказом
        res_supervise = subprocess.run(
            ["bash", str(self.wolf_sh), "supervise"],
            capture_output=True, text=True, timeout=15, env=env_fast,
        )
        self.assertNotEqual(res_supervise.returncode, 0)
        # supervise.lock не должен остаться заблокированным
        res_sup_free = subprocess.run(
            ["flock", "-n", str(self.l_dir / "supervise.lock"), "echo", "free"],
            capture_output=True, text=True,
        )
        self.assertEqual(res_sup_free.returncode, 0)

        # Освобождаем lock
        lock_holder.terminate()
        lock_holder.wait(timeout=2)
        self.running_pids.remove(lock_holder.pid)

    def test_grant_cleanup_releases_descriptor_on_all_exit_paths(self):
        """grant_cleanup освобождает дескриптор session.lock на каждом пути выхода."""
        if self._run_in_docker_if_needed("test_grant_cleanup_releases_descriptor_on_all_exit_paths"):
            return

        # Инициализируем сессию
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True)
        run_id = (self.l_dir / "run_id").read_text().strip()

        # Путь 1: пустые параметры -> отказ, lock свободен
        r1 = subprocess.run(["bash", str(self.wolf_sh), "grant-cleanup", "", "", ""], capture_output=True)
        self.assertNotEqual(r1.returncode, 0)
        res_lock = subprocess.run(["flock", "-n", str(self.l_dir / "session.lock"), "true"])
        self.assertEqual(res_lock.returncode, 0, "Lock остался заблокирован после пустых параметров")

        # Путь 2: неверный run_id -> отказ, lock свободен
        r2 = subprocess.run(["bash", str(self.wolf_sh), "grant-cleanup", "1001", "bad_run", "drive_fid_42"], capture_output=True)
        self.assertNotEqual(r2.returncode, 0)
        res_lock = subprocess.run(["flock", "-n", str(self.l_dir / "session.lock"), "true"])
        self.assertEqual(res_lock.returncode, 0, "Lock остался заблокирован после неверного run_id")

        # Путь 3: неверный iid -> отказ, lock свободен
        r3 = subprocess.run(["bash", str(self.wolf_sh), "grant-cleanup", "9999", run_id, "drive_fid_42"], capture_output=True)
        self.assertNotEqual(r3.returncode, 0)
        res_lock = subprocess.run(["flock", "-n", str(self.l_dir / "session.lock"), "true"])
        self.assertEqual(res_lock.returncode, 0, "Lock остался заблокирован после неверного iid")

        # Путь 4: неверный folder_id -> отказ, lock свободен
        r4 = subprocess.run(["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "foreign_fid"], capture_output=True)
        self.assertNotEqual(r4.returncode, 0)
        res_lock = subprocess.run(["flock", "-n", str(self.l_dir / "session.lock"), "true"])
        self.assertEqual(res_lock.returncode, 0, "Lock остался заблокирован после неверного folder_id")

        # Путь 5: успешная выдача -> успех, lock свободен
        r5 = subprocess.run(["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "drive_fid_42"], capture_output=True)
        self.assertEqual(r5.returncode, 0)
        res_lock = subprocess.run(["flock", "-n", str(self.l_dir / "session.lock"), "true"])
        self.assertEqual(res_lock.returncode, 0, "Lock остался заблокирован после успешной выдачи")

    def test_server_storage_determination_timeout_bounded(self):
        """Общее время работы storage_id ограничено и не зависает при недоступности хранилища."""
        if self._run_in_docker_if_needed("test_server_storage_determination_timeout_bounded"):
            return

        # Имитируем зависание rclone на 30 секунд
        env_hang = dict(os.environ, HANG_RCLONE="1")
        t0 = time.time()
        res = subprocess.run(
            ["bash", str(self.wolf_sh), "storage-id"],
            capture_output=True, text=True, env=env_hang,
        )
        elapsed = time.time() - t0

        # Команда обязана завершиться ошибкой за время около 10 секунд (не 30+)
        self.assertNotEqual(res.returncode, 0)
        self.assertLess(elapsed, 16.0, f"storage_id выполнялся слишком долго: {elapsed:.1f} с")

    def test_delayed_worker_holds_lock_and_prevents_new_coordinator(self):
        """Управляемый работник задерживает выход по TERM:
        - пока работник не завершился, второй координатор получает отказ;
        - после завершения новый координатор запускается;
        - старых работников не остаётся;
        - прежний grant недействителен."""
        if self._run_in_docker_if_needed("test_delayed_worker_holds_lock_and_prevents_new_coordinator"):
            return

        env_delay = dict(os.environ, DELAY_WORKER_WATCH="1")
        p1 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env_delay,
        )
        self.running_pids.append(p1.pid)

        # Ждем старта сессии и запуска работника watch
        run_id_file = self.l_dir / "run_id"
        watch_pid_file = self.l_dir / "watch.pid"
        t0 = time.time()
        while (not run_id_file.exists() or not watch_pid_file.exists()) and time.time() - t0 < 5.0:
            time.sleep(0.05)
        self.assertTrue(run_id_file.exists())
        self.assertTrue(watch_pid_file.exists())
        run_id_1 = run_id_file.read_text().strip()
        watch_pid = int(watch_pid_file.read_text().strip())
        self.running_pids.append(watch_pid)

        # Убеждаемся, что работник реально жив
        os.kill(watch_pid, 0)

        # Выдаем разрешение для сессии 1
        res_grant = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id_1, "drive_fid_42"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(res_grant.returncode, 0)
        grant_file = self.l_dir / "cleanup_grant.json"
        self.assertTrue(grant_file.exists())

        # Посылаем SIGTERM координатору 1
        p1.terminate()

        # Даём сигналу дойти до watch-работника, который входит в 2-секундную задержку
        time.sleep(0.3)
        try:
            os.kill(watch_pid, 0)
            worker_alive_during_delay = True
        except ProcessLookupError:
            worker_alive_during_delay = False
        self.assertTrue(worker_alive_during_delay, "Работник должен быть жив во время задержки")

        # 1. Пока работник не завершился, второй координатор ОБЯЗАН получить отказ
        p2 = subprocess.run(
            ["bash", str(self.wolf_sh), "supervise"],
            capture_output=True, text=True, timeout=1,
        )
        self.assertNotEqual(p2.returncode, 0, "Второй координатор должен получить отказ, пока работник 1 задерживается")

        # 2. Ждем штатного завершения первого координатора
        p1.wait(timeout=5)
        self.assertEqual(p1.returncode, 0)
        if p1.pid in self.running_pids:
            self.running_pids.remove(p1.pid)

        # 3. Старых работников не остаётся
        self._assert_process_terminated(watch_pid, msg="Старый работник watch должен быть мёртв")
        if watch_pid in self.running_pids:
            self.running_pids.remove(watch_pid)

        # 4. После завершения новый координатор запускается
        p3 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.running_pids.append(p3.pid)

        t0 = time.time()
        while time.time() - t0 < 5.0:
            if run_id_file.exists() and run_id_file.read_text().strip() != run_id_1:
                break
            time.sleep(0.05)
        run_id_2 = run_id_file.read_text().strip()
        self.assertNotEqual(run_id_1, run_id_2, "Новый координатор должен создать новый run_id")

        # 5. Прежний grant недействителен (удален)
        self.assertFalse(grant_file.exists(), "Прежний grant должен быть удален при старте нового координатора")

        p3.terminate()
        p3.wait(timeout=5)
        if p3.pid in self.running_pids:
            self.running_pids.remove(p3.pid)

    def test_normal_stop_terminates_all_child_processes(self):
        """Обычная остановка без задержки гарантированно завершает всех работников (web, watch, boot),
        а не просто процесс координатора."""
        if self._run_in_docker_if_needed("test_normal_stop_terminates_all_child_processes"):
            return

        p1 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.running_pids.append(p1.pid)

        # Ждем запуска всех работников и появления их pid-файлов
        web_pid_file = self.l_dir / "web.pid"
        watch_pid_file = self.l_dir / "watch.pid"
        boot_pid_file = self.l_dir / "boot.pid"

        t0 = time.time()
        while (not web_pid_file.exists() or not watch_pid_file.exists() or not boot_pid_file.exists()) and time.time() - t0 < 5.0:
            time.sleep(0.05)

        self.assertTrue(web_pid_file.exists(), "web.pid не создан")
        self.assertTrue(watch_pid_file.exists(), "watch.pid не создан")
        self.assertTrue(boot_pid_file.exists(), "boot.pid не создан")

        web_pid = int(web_pid_file.read_text().strip())
        watch_pid = int(watch_pid_file.read_text().strip())
        boot_pid = int(boot_pid_file.read_text().strip())

        for c_pid in [web_pid, watch_pid, boot_pid]:
            self.running_pids.append(c_pid)
            os.kill(c_pid, 0)  # Все работники подтвержденно живы

        # Остановка координатора
        p1.terminate()
        p1.wait(timeout=5)
        self.assertEqual(p1.returncode, 0)
        if p1.pid in self.running_pids:
            self.running_pids.remove(p1.pid)

        # Проверяем КАЖДЫЙ дочерний процесс — ни один не должен остаться жив
        for c_pid, name in [(web_pid, "web"), (watch_pid, "watch"), (boot_pid, "boot")]:
            self._assert_process_terminated(c_pid, msg=f"Работник {name} (PID {c_pid}) остался жив после остановки supervise")
            if c_pid in self.running_pids:
                self.running_pids.remove(c_pid)

        # supervise.lock свободен
        res_lock = subprocess.run(["flock", "-n", str(self.l_dir / "supervise.lock"), "true"])
        self.assertEqual(res_lock.returncode, 0, "supervise.lock должен освободиться после остановки")

    def test_process_tree_grandchild_holds_lock_until_stopped(self):
        """Тест с настоящим деревом процессов:
        координатор → обёртка → работник → дочерний процесс (запись).
        Родитель (работник) завершается раньше потомка.
        Пока потомок жив, новый координатор не запускается.
        После успешной остановки всего дерева запуск разрешён."""
        if self._run_in_docker_if_needed("test_process_tree_grandchild_holds_lock_until_stopped"):
            return

        env_tree = dict(os.environ, TREE_WORKER_WATCH="1")
        p1 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env_tree,
        )
        self.running_pids.append(p1.pid)

        # Ждем старта сессии и появления child_writer.pid
        child_writer_file = self.l_dir / "child_writer.pid"
        run_id_file = self.l_dir / "run_id"
        t0 = time.time()
        while (not run_id_file.exists() or not child_writer_file.exists()) and time.time() - t0 < 5.0:
            time.sleep(0.05)

        self.assertTrue(run_id_file.exists(), "run_id не был создан")
        self.assertTrue(child_writer_file.exists(), "child_writer.pid не был создан")
        child_writer_pid = int(child_writer_file.read_text().strip())
        self.running_pids.append(child_writer_pid)

        # Проверяем: потомок реально жив
        os.kill(child_writer_pid, 0)

        # Даем родителю время завершиться
        time.sleep(0.1)

        # Останавливаем координатор
        p1.terminate()

        # Даем сигналу дойти до дерева процессов (потомок перехватывает TERM и засыпает на 1.5 с)
        time.sleep(0.3)
        try:
            os.kill(child_writer_pid, 0)
            child_alive_during_teardown = True
        except ProcessLookupError:
            child_alive_during_teardown = False

        self.assertTrue(child_alive_during_teardown, "Дочерний процесс должен быть жив во время остановки")

        # 1. Пока дочерний процесс жив, второй координатор ОБЯЗАН получить отказ
        p2 = subprocess.run(
            ["bash", str(self.wolf_sh), "supervise"],
            capture_output=True, text=True, timeout=1,
        )
        self.assertNotEqual(p2.returncode, 0, "Второй координатор не должен запускаться, пока жив потомок")

        # 2. Ждем штатного завершения первого координатора
        p1.wait(timeout=5)
        self.assertEqual(p1.returncode, 0)
        if p1.pid in self.running_pids:
            self.running_pids.remove(p1.pid)

        # 3. Весь дочерний процесс гарантированно остановлен
        self._assert_process_terminated(child_writer_pid, msg="Дочерний процесс записи должен быть завершён")
        if child_writer_pid in self.running_pids:
            self.running_pids.remove(child_writer_pid)

        # 4. После остановки всего дерева запуск нового координатора разрешён
        p3 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.running_pids.append(p3.pid)

        # Ждем старта нового координатора
        time.sleep(0.3)
        res_lock_held = subprocess.run(
            ["flock", "-n", str(self.l_dir / "supervise.lock"), "echo", "held"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(res_lock_held.returncode, 0, "Новый координатор должен успешно захватить блокировку")

        p3.terminate()
        p3.wait(timeout=5)
        if p3.pid in self.running_pids:
            self.running_pids.remove(p3.pid)

    def test_unconfirmed_worker_teardown_blocks_future_coordinator(self):
        """Ветка, где завершение после принудительной остановки не подтверждено:
        - координатор сообщает об ошибке (exit != 0);
        - блокировка не считается успешно освобождённой;
        - запуск нового координатора строго заблокирован;
        - отказ происходит сразу без бесконечного ожидания."""
        if self._run_in_docker_if_needed("test_unconfirmed_worker_teardown_blocks_future_coordinator"):
            return

        env_short = dict(os.environ, WOLF_STOP_TIMEOUT_SEC="1")
        p1 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env_short,
        )
        self.running_pids.append(p1.pid)

        # Ждем старта сессии и запуска работников
        run_id_file = self.l_dir / "run_id"
        web_pid_file = self.l_dir / "web.pid"
        t0 = time.time()
        while (not run_id_file.exists() or not web_pid_file.exists()) and time.time() - t0 < 5.0:
            time.sleep(0.05)
        self.assertTrue(run_id_file.exists())
        self.assertTrue(web_pid_file.exists())

        # Активируем контролируемую подмену проверки: работники остаются якобы живыми
        override_file = self.l_dir / "supervise_alive_override"
        override_file.touch()

        # Посылаем сигнал остановки координатору
        p1.terminate()
        # Координатор обязан завершиться с кодом ошибки (НЕ 0)
        p1.wait(timeout=5)
        self.assertNotEqual(p1.returncode, 0, "Координатор должен сообщить об ошибке при неподтверждённых работниках")
        if p1.pid in self.running_pids:
            self.running_pids.remove(p1.pid)

        # Зафиксировано состояние явного отказа
        failed_file = self.l_dir / "supervise.failed"
        self.assertTrue(failed_file.exists(), "supervise.failed должен быть создан")

        # Попытка запуска нового координатора ОБЯЗАНА сразу завершиться отказом
        t_start = time.time()
        p2 = subprocess.run(
            ["bash", str(self.wolf_sh), "supervise"],
            capture_output=True, text=True, timeout=2,
        )
        t_elapsed = time.time() - t_start
        self.assertNotEqual(p2.returncode, 0, "Новый координатор не должен запускаться при supervise.failed")
        self.assertLess(t_elapsed, 1.5, "Отказ должен происходить мгновенно без бесконечного ожидания")

        # Снимаем подмену и очищаем аварийный маркер напрямую в фикстуре теста
        override_file.unlink(missing_ok=True)
        failed_file.unlink(missing_ok=True)
        self.assertFalse(failed_file.exists())

        # Теперь координатор успешно запускается
        p3 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.running_pids.append(p3.pid)
        time.sleep(0.3)
        res_lock_held = subprocess.run(
            ["flock", "-n", str(self.l_dir / "supervise.lock"), "echo", "held"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(res_lock_held.returncode, 0)

        p3.terminate()
        p3.wait(timeout=5)
        if p3.pid in self.running_pids:
            self.running_pids.remove(p3.pid)

    def test_race_coordinator_precheck_and_failed_state(self):
        """Управляемый тест гонки:
        - второй координатор прошёл предварительную проверку отсутствия supervise.failed и приостановлен;
        - первый сохраняет аварийное состояние и завершается, освобождая файловую блокировку через ядро;
        - второй продолжает выполнение и захватывает блокировку;
        - под блокировкой повторная проверка supervise.failed возвращает отказ;
        - отказ происходит без смены run_id, выдачи разрешения и запуска работников."""
        if self._run_in_docker_if_needed("test_race_coordinator_precheck_and_failed_state"):
            return

        # 1. Первый координатор запускается штатно
        env_p1 = dict(os.environ, WOLF_STOP_TIMEOUT_SEC="1")
        p1 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env_p1,
        )
        self.running_pids.append(p1.pid)

        run_id_file = self.l_dir / "run_id"
        web_pid_file = self.l_dir / "web.pid"
        t0 = time.time()
        while (not run_id_file.exists() or not web_pid_file.exists()) and time.time() - t0 < 5.0:
            time.sleep(0.05)
        self.assertTrue(run_id_file.exists())
        self.assertTrue(web_pid_file.exists())
        run_id_1 = run_id_file.read_text().strip()

        # Выдаем разрешение для сессии 1
        res_grant = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id_1, "drive_fid_42"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(res_grant.returncode, 0)
        grant_file = self.l_dir / "cleanup_grant.json"
        self.assertTrue(grant_file.exists())

        # На этом этапе supervise.failed отсутствует
        failed_file = self.l_dir / "supervise.failed"
        self.assertFalse(failed_file.exists())

        # 2. Второй координатор запускается с паузой после предварительной проверки
        race_done_file = self.l_dir / "race_precheck_done"
        race_resume_file = self.l_dir / "race_resume"
        race_done_file.unlink(missing_ok=True)
        race_resume_file.unlink(missing_ok=True)

        env_race = dict(os.environ, WOLF_TEST_RACE_PAUSE="1")
        p2 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env_race,
        )
        self.running_pids.append(p2.pid)

        # Ждем, пока второй координатор подтвердит прохождение предварительной проверки
        t0 = time.time()
        while not race_done_file.exists() and time.time() - t0 < 5.0:
            time.sleep(0.02)
        self.assertTrue(race_done_file.exists(), "Второй координатор не завершил предварительную проверку")

        # 3. Первый координатор переходит в аварийную остановку с неподтверждёнными работниками
        override_file = self.l_dir / "supervise_alive_override"
        override_file.touch()

        p1.terminate()
        p1.wait(timeout=5)
        self.assertNotEqual(p1.returncode, 0)
        if p1.pid in self.running_pids:
            self.running_pids.remove(p1.pid)

        # Первый координатор сохранил supervise.failed и завершился (ядро освободило flock на supervise.lock)
        self.assertTrue(failed_file.exists())

        # 4. Возобновляем второго координатора
        race_resume_file.touch()

        # Второй координатор захватывает supervise.lock, обнаруживает supervise.failed под блокировкой и выходит с отказом
        p2.wait(timeout=5)
        self.assertNotEqual(p2.returncode, 0, "Второй координатор должен получить отказ при повторной проверке под блокировкой")
        if p2.pid in self.running_pids:
            self.running_pids.remove(p2.pid)

        # 5. Проверка: отказ без смены run_id, выдачи разрешения и запуска работников
        self.assertEqual(run_id_file.read_text().strip(), run_id_1, "run_id не должен был измениться")
        self.assertTrue(grant_file.exists())
        self.assertEqual(json.loads(grant_file.read_text())["run_id"], run_id_1)

        # supervise.lock корректно освобождён вторым координатором
        res_lock_free = subprocess.run(["flock", "-n", str(self.l_dir / "supervise.lock"), "echo", "free"], capture_output=True, text=True)
        self.assertEqual(res_lock_free.returncode, 0)

        # Очистка маркера
        override_file.unlink(missing_ok=True)
        failed_file.unlink(missing_ok=True)

    def test_marker_write_error_holds_lock_and_prevents_new_run(self):
        """Проверка ошибки записи аварийного маркера:
        - при ошибке записи supervise.failed координатор не освобождает блокировку и не выходит;
        - удержание блокировки исключает разрешение нового запуска;
        - второй координатор получает отказ."""
        if self._run_in_docker_if_needed("test_marker_write_error_holds_lock_and_prevents_new_run"):
            return

        env_marker_fail = dict(os.environ, WOLF_STOP_TIMEOUT_SEC="1", WOLF_TEST_FAIL_MARKER_ERROR="1")
        p1 = subprocess.Popen(
            ["bash", str(self.wolf_sh), "supervise"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env_marker_fail,
        )
        self.running_pids.append(p1.pid)

        run_id_file = self.l_dir / "run_id"
        web_pid_file = self.l_dir / "web.pid"
        t0 = time.time()
        while (not run_id_file.exists() or not web_pid_file.exists()) and time.time() - t0 < 5.0:
            time.sleep(0.05)
        self.assertTrue(run_id_file.exists())
        self.assertTrue(web_pid_file.exists())
        run_id_1 = run_id_file.read_text().strip()

        # Активируем неподтверждённых работников
        override_file = self.l_dir / "supervise_alive_override"
        override_file.touch()

        # Инициируем остановку координатора 1
        p1.terminate()

        # Даем время на таймаут остановки (1 с) и попытку сохранения маркера
        time.sleep(2.0)

        # Проверяем: координатор 1 НЕ завершился, так как маркер не удалось сохранить,
        # и выход привёл бы к неконтролируемому освобождению блокировки ядром
        self.assertIsNone(p1.poll(), "Координатор 1 не должен был завершиться при сбое записи маркера")

        # Маркер физически отсутствует (ошибка записи смоделирована)
        failed_file = self.l_dir / "supervise.failed"
        self.assertFalse(failed_file.exists())

        # Второй координатор пытается запуститься -> отказ, так как блокировка удерживается координатором 1
        p2 = subprocess.run(
            ["bash", str(self.wolf_sh), "supervise"],
            capture_output=True, text=True, timeout=2,
        )
        self.assertNotEqual(p2.returncode, 0, "Второй координатор не должен запускаться, пока блокировка удерживается")

        # Состояние сессии 1 не повреждено
        self.assertEqual(run_id_file.read_text().strip(), run_id_1)

        # Очистка: принудительно останавливаем координатора 1
        p1.kill()
        p1.wait(timeout=2)
        if p1.pid in self.running_pids:
            self.running_pids.remove(p1.pid)
        override_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

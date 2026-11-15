# Сторож обновления Windows (updater.WATCH_WIN) — настоящий PowerShell на подставных файлах: «установщик» — .cmd,
# который кончает с нужным кодом и пишет, с какими параметрами его запустили; «VastGame» — .cmd, который отмечает,
# что его открыли. Только в Windows (на Mac пропускается); остальное про обновление — tests/test_updater.py.

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "только Windows")
class TestWatchWindows(unittest.TestCase):
    def run_watch(self, code, old_pid=None, busy=False, mark_started=True,
                  corrupt_install=False, broken_backup=False, stale_started=False,
                  crash_new=False):
        d = Path(tempfile.mkdtemp())
        folder = d / "update"
        folder.mkdir()
        setup = folder / "VastGame-Setup-9.9.9.exe.cmd"
        exe = d / "VastGame.cmd"
        canary = d / "canary.txt"
        canary.write_text("version-1.0.0\n", encoding="utf-8")

        # Исходный (прежний) исполняемый файл:
        exe.write_text(f'@echo opened > "{d}\\opened"\n')

        # Старая метка started (если проверяем защиту от устаревшей метки):
        if stale_started:
            (folder / "started").write_text("stale_started\n", encoding="utf-8")

        # Логика установщика:
        setup_lines = [f'@echo %* > "{d}\\setup-args"']
        if code == 0:
            setup_lines.append(f'@echo version-9.9.9 > "{d}\\canary.txt"')
            if crash_new:
                # Новое приложение падает с кодом 42 без отметки started
                setup_lines.append(f'@echo @echo crashed ^> "{folder}\\opened-crashed" > "{exe}"')
                setup_lines.append(f'@echo @exit /b 42 >> "{exe}"')
            elif mark_started:
                # Новое приложение штатно открывается и ставит started
                setup_lines.append(f'@echo @echo opened ^> "{d}\\opened" > "{exe}"')
                setup_lines.append(f'@echo @type nul ^> "{folder}\\started" >> "{exe}"')
            else:
                # Новое приложение запустилось, но не подтвердило готовность (зависло/не отметилось)
                setup_lines.append(f'@echo @echo opened ^> "{d}\\opened" > "{exe}"')
            setup_lines.append('@exit /b 0')
        else:
            if corrupt_install:
                setup_lines.append(f'@echo corrupt-version > "{d}\\canary.txt"')
            setup_lines.append(f'@exit /b {code}')

        setup.write_text("\n".join(setup_lines) + "\n", encoding="utf-8")

        stray = None
        if busy:                                 # «другой VastGame» из папки программы, который сам не закроется
            shutil.copy(Path(os.environ["WINDIR"]) / "System32" / "PING.EXE", d / "stray.exe")
            stray = subprocess.Popen([str(d / "stray.exe"), "-n", "300", "127.0.0.1"], stdout=subprocess.DEVNULL)

        lock_handle = None
        if broken_backup:
            # Создаём заблокированный файл в d, чтобы Copy-Item в backup упал с ошибкой
            locked_file = d / "locked_for_backup.txt"
            locked_file.write_text("locked", encoding="utf-8")
            lock_handle = open(locked_file, "r+b", buffering=0)
            try:
                import msvcrt
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 6)
            except Exception:
                pass

        pid = old_pid or subprocess.run(["cmd", "/c", "exit"]).returncode or 999999    # прежнее окно уже закрыто
        env = dict(os.environ, VG_FOLDER=str(folder), VG_PID=str(pid), VG_SETUP=str(setup), VG_VERSION="9.9.9",
                   VG_EXE=str(exe), VG_WAIT="2")
        t0 = time.time()
        # Ровно как запускает программа (updater._install_win): с теми же флагами — без консоли PowerShell молчал
        p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-EncodedCommand", base64.b64encode(updater.WATCH_WIN.encode("utf-16-le")).decode()],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **updater.watch_flags())
        p.wait(120)

        if lock_handle:
            try:
                import msvcrt
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 6)
            except Exception:
                pass
            lock_handle.close()

        for _ in range(50):                      # «VastGame» запущен без ожидания — дать ему отметиться
            if (d / "opened").exists():
                break
            time.sleep(0.2)
        if stray:
            try:
                self.stray = stray.wait(10)      # сторож его закрыл
            except subprocess.TimeoutExpired:
                stray.kill()
                self.fail("сторож не закрыл другую копию из папки программы")
        return d, folder, setup, time.time() - t0

    def test_installed(self):
        d, folder, setup, _ = self.run_watch(0)
        args = (d / "setup-args").read_text(encoding="utf-8")
        for a in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG="):
            self.assertIn(a, args)
        self.assertTrue((d / "opened").exists())                       # открыли VastGame
        self.assertFalse(setup.exists())                               # установщик убран
        self.assertFalse((folder / "failed").exists())
        self.assertFalse((folder / "backup").exists())                 # резервная копия удалена
        self.assertEqual((d / "canary.txt").read_text(encoding="utf-8").strip(), "version-9.9.9")
        self.assertIn("поставлена 9.9.9", (folder / "update.log").read_text(encoding="utf-8-sig"))

    def test_setup_failed(self):
        d, folder, setup, _ = self.run_watch(5, corrupt_install=True)
        self.assertTrue((d / "opened").exists())                       # прежнюю версию восстановили и открыли
        self.assertEqual((folder / "failed").read_text(encoding="utf-8-sig").strip(), "9.9.9")
        self.assertTrue(setup.exists())
        self.assertEqual((d / "canary.txt").read_text(encoding="utf-8").strip(), "version-1.0.0")  # откат из backup!
        self.assertIn("код 5", (folder / "update.log").read_text(encoding="utf-8-sig"))

    def test_new_version_did_not_start(self):
        # Новая версия установилась, но упала без отметки started -> откат к 1.0.0
        d, folder, setup, _ = self.run_watch(0, crash_new=True)
        self.assertTrue((d / "opened").exists())                       # прежняя версия запущена после отката
        self.assertTrue((folder / "opened-crashed").exists())          # новая версия действительно падала
        self.assertEqual((folder / "failed").read_text(encoding="utf-8-sig").strip(), "9.9.9")
        self.assertEqual((d / "canary.txt").read_text(encoding="utf-8").strip(), "version-1.0.0")  # откат из backup!
        log = (folder / "update.log").read_text(encoding="utf-8-sig")
        self.assertIn("без подтверждения готовности (код 42)", log)

    def test_backup_failure_aborts(self):
        # Если резервную копию создать не удалось, установка не запускается вообще
        d, folder, setup, _ = self.run_watch(0, broken_backup=True)
        self.assertFalse((d / "setup-args").exists())                  # установщик НЕ запускался
        self.assertTrue((d / "opened").exists())                       # исходная версия перезапущена
        self.assertEqual((folder / "failed").read_text(encoding="utf-8-sig").strip(), "9.9.9")
        self.assertEqual((d / "canary.txt").read_text(encoding="utf-8").strip(), "version-1.0.0")
        self.assertIn("не удалось создать резервную копию", (folder / "update.log").read_text(encoding="utf-8-sig"))

    def test_stale_started_ignored(self):
        # Старая отметка started удаляется сторожем и не ложно-подтверждает сломанное обновление
        d, folder, setup, _ = self.run_watch(0, mark_started=False, stale_started=True)
        self.assertTrue((d / "opened").exists())                       # исходная версия перезапущена после отката
        self.assertEqual((folder / "failed").read_text(encoding="utf-8-sig").strip(), "9.9.9")
        self.assertEqual((d / "canary.txt").read_text(encoding="utf-8").strip(), "version-1.0.0")
        log = (folder / "update.log").read_text(encoding="utf-8-sig")
        self.assertIn("не подтвердила запуск", log)

    def test_closes_other_copies_first(self):
        d, folder, setup, _ = self.run_watch(0, busy=True)
        self.assertIsNotNone(self.stray)
        self.assertTrue((d / "opened").exists())
        log = (folder / "update.log").read_text(encoding="utf-8-sig")
        self.assertIn("закрываю stray.exe", log)
        self.assertIn("поставлена 9.9.9", log)

    def test_waits_for_the_old_window(self):
        old = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", "Start-Sleep 3"])
        d, folder, setup, took = self.run_watch(0, old_pid=old.pid)
        old.wait()
        self.assertGreaterEqual(took, 2)                                # установщик — только после выхода окна
        self.assertTrue((d / "opened").exists())

    def test_w1_restore_interrupted_preserves_backup_and_does_not_launch(self):
        """W1: Восстановление прерывается при ошибке копирования/удаления.
        Исправный backup сохранён, повреждённое приложение НЕ запускается."""
        d = Path(tempfile.mkdtemp())
        folder = d / "update"
        folder.mkdir()
        exe = d / "VastGame.cmd"
        exe.write_text(f'@echo opened > "{d}\\opened"\n')
        canary = d / "canary.txt"
        canary.write_text("original-v1\n", encoding="utf-8")

        # Инсталлятор: повреждает canary.txt, запускает фоновый процесс с блокировкой canary.txt и выходит с кодом 5
        locker_py = folder / "lock.py"
        locker_py.write_text("""import sys, time, msvcrt, os
f = open(sys.argv[1], 'r+b')
msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
with open(sys.argv[2], 'w') as ready:
    ready.write('ready')
stop_file = sys.argv[3]
while not os.path.exists(stop_file):
    time.sleep(0.1)
""", encoding="utf-8")

        ready_marker = folder / "locked.ready"
        stop_marker = folder / "locked.stop"
        py_exe = sys.executable

        setup = folder / "Setup.cmd"
        setup.write_text(f"""@echo corrupted-partially > "{canary}"
start /b "" "{py_exe}" "{locker_py}" "{canary}" "{ready_marker}" "{stop_marker}"
:wait_ready
if not exist "{ready_marker}" (
    ping -n 1 127.0.0.1 >nul
    goto wait_ready
)
@exit /b 5
""", encoding="utf-8")

        env = dict(os.environ, VG_FOLDER=str(folder), VG_PID="999999", VG_SETUP=str(setup),
                   VG_VERSION="2.0.0", VG_EXE=str(exe), VG_WAIT="2")
        p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-EncodedCommand", base64.b64encode(updater.WATCH_WIN.encode("utf-16-le")).decode()],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **updater.watch_flags())
        p.wait(30)

        # Снимаем блокировку
        stop_marker.write_text("stop", encoding="utf-8")

        backup = folder / "backup"
        # Проверки W1:
        # 1. Приложение НЕ запущено:
        self.assertFalse((d / "opened").exists())
        # 2. Исправный backup сохранён и содержит оригинальные файлы:
        self.assertTrue(backup.exists())
        self.assertEqual((backup / "canary.txt").read_text(encoding="utf-8").strip(), "original-v1")
        # 3. Состояние restore_failed зафиксировано:
        state = (folder / "backup_state").read_text(encoding="utf-8").strip()
        self.assertEqual(state, "restore_failed")
        log = (folder / "update.log").read_text(encoding="utf-8-sig")
        self.assertIn("восстановление не завершено — запуск повреждённого приложения отменён", log)

    def test_w2_resume_after_w1_succeeds(self):
        """W2: Повтор после устранения блокировки W1 через VG_RESUME=1 восстанавливает файлы и запускает приложение."""
        d = Path(tempfile.mkdtemp())
        folder = d / "update"
        folder.mkdir()
        backup = folder / "backup"
        backup.mkdir()
        exe = d / "VastGame.cmd"
        # Восстанавливаемый файл из backup:
        (backup / "VastGame.cmd").write_text(f'@echo opened > "{d}\\opened"\n@type nul > "{folder}\\started"\n')
        (backup / "canary.txt").write_text("original-v1\n", encoding="utf-8")
        (folder / "backup_state").write_text("restore_failed\n", encoding="utf-8")
        (folder / "failed").write_text("restore_failed\n", encoding="utf-8")

        # В целевом каталоге d — повреждённое состояние:
        (d / "canary.txt").write_text("corrupted-partially\n", encoding="utf-8")

        env = dict(os.environ, VG_FOLDER=str(folder), VG_PID="999999", VG_RESUME="1",
                   VG_VERSION="restore", VG_EXE=str(exe), VG_WAIT="5")
        p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-EncodedCommand", base64.b64encode(updater.WATCH_WIN.encode("utf-16-le")).decode()],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **updater.watch_flags())
        p.wait(30)

        for _ in range(30):
            if (d / "opened").exists():
                break
            time.sleep(0.2)

        # Проверки W2:
        # 1. Файлы восстановлены из сохранённого backup:
        self.assertEqual((d / "canary.txt").read_text(encoding="utf-8").strip(), "original-v1")
        # 2. Настоящее приложение запущено:
        self.assertTrue((d / "opened").exists())
        # 3. Резервная копия удалена после подтверждения готовности:
        self.assertFalse(backup.exists())

    def test_w3_new_update_blocked_when_restore_incomplete(self):
        """W3: Попытка нового обновления при незавершённом восстановлении блокируется, backup не удаляется."""
        d = Path(tempfile.mkdtemp())
        folder = d / "update"
        folder.mkdir()
        backup = folder / "backup"
        backup.mkdir()
        (backup / "canary.txt").write_text("safe-backup-content\n", encoding="utf-8")
        (folder / "backup_state").write_text("restore_failed\n", encoding="utf-8")

        setup = folder / "Setup.cmd"
        setup.write_text(f'@echo executed > "{d}\\setup-executed"\n', encoding="utf-8")
        exe = d / "VastGame.cmd"
        exe.write_text('@exit /b 0\n')

        env = dict(os.environ, VG_FOLDER=str(folder), VG_PID="999999", VG_SETUP=str(setup),
                   VG_VERSION="3.0.0", VG_EXE=str(exe), VG_WAIT="2")
        p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-EncodedCommand", base64.b64encode(updater.WATCH_WIN.encode("utf-16-le")).decode()],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **updater.watch_flags())
        p.wait(30)

        # Проверки W3:
        # 1. Установщик НЕ запускался:
        self.assertFalse((d / "setup-executed").exists())
        # 2. Backup НЕ удалён и не изменён:
        self.assertTrue(backup.exists())
        self.assertEqual((backup / "canary.txt").read_text(encoding="utf-8").strip(), "safe-backup-content")
        # 3. Сообщено об ошибке:
        self.assertEqual((folder / "failed").read_text(encoding="utf-8-sig").strip(), "restore_incomplete")
        log = (folder / "update.log").read_text(encoding="utf-8-sig")
        self.assertIn("предыдущее восстановление не завершено — запуск нового обновления заблокирован", log)

    def test_w4_unknown_old_backup_blocks_update(self):
        """W4: Неизвестная или неполная резервная копия блокирует новое обновление, данные не удаляются."""
        d = Path(tempfile.mkdtemp())
        folder = d / "update"
        folder.mkdir()
        backup = folder / "backup"
        backup.mkdir()
        (backup / "canary.txt").write_text("unknown-backup-content\n", encoding="utf-8")
        # backup_state установлен в backing_up (оборванная запись)
        (folder / "backup_state").write_text("backing_up\n", encoding="utf-8")

        setup = folder / "Setup.cmd"
        setup.write_text(f'@echo executed > "{d}\\setup-executed"\n', encoding="utf-8")
        exe = d / "VastGame.cmd"
        exe.write_text('@exit /b 0\n')

        env = dict(os.environ, VG_FOLDER=str(folder), VG_PID="999999", VG_SETUP=str(setup),
                   VG_VERSION="3.0.0", VG_EXE=str(exe), VG_WAIT="2")
        p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-EncodedCommand", base64.b64encode(updater.WATCH_WIN.encode("utf-16-le")).decode()],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **updater.watch_flags())
        p.wait(30)

        # Проверки W4:
        # 1. Установщик НЕ запускался:
        self.assertFalse((d / "setup-executed").exists())
        # 2. Backup НЕ удалён:
        self.assertTrue(backup.exists())
        self.assertEqual((backup / "canary.txt").read_text(encoding="utf-8").strip(), "unknown-backup-content")
        # 3. Сообщено об ошибке:
        self.assertEqual((folder / "failed").read_text(encoding="utf-8-sig").strip(), "backup_unknown")
        log = (folder / "update.log").read_text(encoding="utf-8-sig")
        self.assertIn("обнаружена неподтверждённая или неполная резервная копия", log)

    def test_w5_resume_invalid_states_rejected_python(self):
        """W5: resume_rollback_win отклоняет недопустимые состояния (backing_up, backup_failed, missing, unknown),
        не трогая файлы программы и сохраняя резервную копию."""
        d = Path(tempfile.mkdtemp())
        folder = d / "update"
        folder.mkdir()
        exe = d / "VastGame.cmd"
        exe.write_text('@exit /b 0\n', encoding="utf-8")
        canary = d / "canary.txt"
        canary.write_text("app-canary-content\n", encoding="utf-8")

        # 1. Резервная копия отсутствует вовсе:
        with self.assertRaises(updater.VastgameError) as cm:
            updater.resume_rollback_win(exe=exe, folder=folder)
        self.assertEqual(cm.exception.key, "update.err.no_backup")
        self.assertEqual(canary.read_text(encoding="utf-8").strip(), "app-canary-content")

        backup = folder / "backup"
        backup.mkdir()
        (backup / "VastGame.cmd").write_text('@exit /b 0\n', encoding="utf-8")
        (backup / "canary.txt").write_text("backup-canary-content\n", encoding="utf-8")
        state_file = folder / "backup_state"

        # 2. Файл состояния отсутствует:
        with self.assertRaises(updater.VastgameError) as cm:
            updater.resume_rollback_win(exe=exe, folder=folder)
        self.assertEqual(cm.exception.key, "update.err.invalid_backup")
        self.assertTrue(backup.exists())
        self.assertEqual(canary.read_text(encoding="utf-8").strip(), "app-canary-content")

        # 3. Состояния backing_up, backup_failed, unknown:
        for invalid_state in ["backing_up", "backup_failed", "unknown_state", "corrupted"]:
            state_file.write_text(f"{invalid_state}\n", encoding="utf-8")
            with self.assertRaises(updater.VastgameError) as cm:
                updater.resume_rollback_win(exe=exe, folder=folder)
            self.assertEqual(cm.exception.key, "update.err.invalid_backup")
            # Проверяем, что файлы программы и backup не тронуты:
            self.assertEqual(canary.read_text(encoding="utf-8").strip(), "app-canary-content")
            self.assertTrue(backup.exists())
            self.assertEqual((backup / "canary.txt").read_text(encoding="utf-8").strip(), "backup-canary-content")

        # 4. Отсутствует исполняемый файл в backup:
        state_file.write_text("ready\n", encoding="utf-8")
        (backup / "VastGame.cmd").unlink()
        with self.assertRaises(updater.VastgameError) as cm:
            updater.resume_rollback_win(exe=exe, folder=folder)
        self.assertEqual(cm.exception.key, "update.err.invalid_backup")
        self.assertEqual(canary.read_text(encoding="utf-8").strip(), "app-canary-content")

    def test_w6_resume_invalid_states_rejected_watcher(self):
        """W6: Ветка VG_RESUME сторожа останавливается без удаления текущих файлов приложения,
        если состояние резервной копии недопустимо (backing_up, backup_failed, missing, unknown)."""
        for invalid_state in ["backing_up", "backup_failed", "corrupted_state", "missing"]:
            d = Path(tempfile.mkdtemp())
            folder = d / "update"
            folder.mkdir()
            exe = d / "VastGame.cmd"
            exe.write_text('@exit /b 0\n', encoding="utf-8")
            app_canary = d / "app_canary.txt"
            app_canary.write_text("untouched-app-data\n", encoding="utf-8")

            backup = folder / "backup"
            backup.mkdir()
            (backup / "VastGame.cmd").write_text('@exit /b 0\n', encoding="utf-8")
            (backup / "backup_canary.txt").write_text("untouched-backup-data\n", encoding="utf-8")

            if invalid_state != "missing":
                (folder / "backup_state").write_text(f"{invalid_state}\n", encoding="utf-8")

            env = dict(os.environ, VG_FOLDER=str(folder), VG_PID="999999", VG_RESUME="1",
                       VG_VERSION="restore", VG_EXE=str(exe), VG_WAIT="2")
            p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                  "-EncodedCommand", base64.b64encode(updater.WATCH_WIN.encode("utf-16-le")).decode()],
                                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 **updater.watch_flags())
            p.wait(30)

            # Проверки для каждого состояния:
            # 1. Сторож завершился с ошибкой (exit code != 0):
            self.assertNotEqual(p.returncode, 0)
            # 2. Файлы программы НЕ удалены и НЕ изменены:
            self.assertTrue(app_canary.exists())
            self.assertEqual(app_canary.read_text(encoding="utf-8").strip(), "untouched-app-data")
            # 3. Резервная копия НЕ удалена и НЕ изменена:
            self.assertTrue(backup.exists())
            self.assertTrue((backup / "backup_canary.txt").exists())
            self.assertEqual((backup / "backup_canary.txt").read_text(encoding="utf-8").strip(), "untouched-backup-data")
            # 4. В журнале зафиксирована остановка без изменения файлов:
            log = (folder / "update.log").read_text(encoding="utf-8-sig")
            self.assertIn("возобновление остановлено без изменения файлов", log)


if __name__ == "__main__":
    unittest.main()

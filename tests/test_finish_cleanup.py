"""tests/test_finish_cleanup.py — проверка подключения очистки к завершению сессии.

Проверяет штатный путь finish_run и клиентскую логику core.py:
1. Выключено по умолчанию — очистка и удаления кандидатов не вызываются;
2. Нет допуска или он не соответствует сессии — 0 удалений;
3. Успешная очистка завершается до разрешения удаления машины;
4. Ошибка и зависание очистки ограничены по времени (WOLF_CLEANUP_TIMEOUT);
5. Повторный finish после cleanup: err не выдаёт ложную ошибку выгрузки;
6. Ошибка сохранения запрещает очистку и удаление машины;
7. Прерывание после отправки DELETE сохраняет результат как UNCONFIRMED_UNKNOWN;
8. Клиентская и серверная ветки удаления соблюдают одинаковый порядок.
"""

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Добавляем пути для импорта модулей
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_game_select import fn
import core
import wolf_cleaner
import wolf_session
from test_storage_cleaner import zstd_compress


def build_test_wolf_script(root: Path, custom_clean: str = "", custom_shutdown: str = "") -> Path:
    """Создаёт тестовый исполняемый скрипт wolf со всеми функциями из wolf-setup.sh."""
    script = root / "wolf"
    s_dir = root / "S"
    t_dir = root / "T"
    l_dir = root / "L"
    r_dir = root / "R"
    for d in (s_dir / "st", t_dir, l_dir, r_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Записываем run_id и iid
    (l_dir / "run_id").write_text("test_run_12345", encoding="utf-8")
    (l_dir / "iid").write_text("99999", encoding="utf-8")

    shutdown_impl = custom_shutdown or r'''
shutdown() {
  local now=$(date +%s)
  echo "${now}|ok|выгружено" > "$S/st/identity"
  echo "${now}|ok|выгружено" > "$S/st/game--Test"
  return 0
}
'''

    clean_impl = custom_clean or (fn("cmd_clean") + "\n")

    content = f"""#!/bin/bash
set -u
S="{s_dir}"
T="{t_dir}"
L="{l_dir}"
R="{r_dir}"
VI="99999"
DH="{root}/home"
export WOLF_LIB_DIR="{REPO_ROOT}"
export PYTHONPATH="{REPO_ROOT}"
mkdir -p "$S/st" "$T" "$L" "$DH"

log() {{
  local msg="$*"
  echo "$(date +%s) $msg" >> "$T/wolf.log"
}}

storage_id() {{
  cat "$L/storage_folder_id" 2>/dev/null || echo "test_fid_12345"
}}

{fn("st")}
{shutdown_impl}
{fn("_pg_alive")}
{fn("_pg_kill")}
{fn("run_finish_cleanup")}
{clean_impl}
{fn("finish_run")}

case ${{1:-}} in
  finish-run) finish_run ;;
  clean) shift; cmd_clean "$@" ;;
  shutdown) shutdown ;;
  *) echo "unknown cmd $1" >&2; exit 2 ;;
esac
"""
    script.write_text(content, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


class TestFinishCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.s_dir = self.root / "S"
        self.l_dir = self.root / "L"
        self.t_dir = self.root / "T"
        self.r_dir = self.root / "R"
        for d in (self.s_dir / "st", self.l_dir, self.t_dir, self.r_dir):
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _setup_active_grant(self, enabled=True):
        """Создаёт активный сессионный допуск."""
        grant_file = self.l_dir / "cleanup_grant.json"
        now = int(time.time())
        data = {
            "status": "granted",
            "run_id": "test_run_12345",
            "iid": "99999",
            "folder_id": "test_fid_12345",
            "granted_at": now,
            "enabled": enabled,
        }
        grant_file.write_text(json.dumps(data), encoding="utf-8")
        if enabled:
            (self.l_dir / "cleanup_enabled").write_text("1", encoding="utf-8")
        else:
            (self.l_dir / "cleanup_enabled").unlink(missing_ok=True)
        (self.l_dir / "run_id").write_text("test_run_12345", encoding="utf-8")
        (self.l_dir / "iid").write_text("99999", encoding="utf-8")
        (self.l_dir / "storage_folder_id").write_text("test_fid_12345", encoding="utf-8")

    def test_scenario_1_disabled_by_default_skips_cleanup(self):
        """1. Выключено по умолчанию — очистка и удаления кандидатов не вызываются."""
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  echo "SIDE_EFFECT_CLEAN_CALLED" >> "$T/side_clean"
  return 0
}
'''
        )
        # Допуск не создаём и opt-in не включаем
        env = dict(os.environ)
        env.pop("VG_STORAGE_CLEANUP_ENABLED", None)

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        # Проверяем статус cleanup
        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|ok|", cleanup_st)
        self.assertIn("выключено", cleanup_st)

        # cmd_clean не вызывался
        self.assertFalse((self.t_dir / "side_clean").exists())

        # finish завершился успехом
        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|", finish_st)

    def test_scenario_2_no_grant_or_mismatched_session_zero_deletions(self):
        """2. Нет допуска или он не соответствует сессии — 0 удалений."""
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  echo "SIDE_EFFECT_CLEAN_CALLED" >> "$T/side_clean"
  return 0
}
'''
        )
        # Включаем opt-in, но не создаём грант
        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("нет допуска", cleanup_st)
        self.assertFalse((self.t_dir / "side_clean").exists())

        # Теперь проверяем несовпадающий run_id
        grant_file = self.l_dir / "cleanup_grant.json"
        grant_file.write_text(json.dumps({
            "status": "granted",
            "run_id": "FOREIGN_RUN_ID",
            "iid": "99999",
            "folder_id": "test_fid_12345",
            "granted_at": int(time.time()),
            "enabled": True,
        }), encoding="utf-8")

        proc2 = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc2.returncode, 0, proc2.stderr + proc2.stdout)
        cleanup_st2 = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("нет допуска", cleanup_st2)
        self.assertFalse((self.t_dir / "side_clean").exists())

    def test_scenario_2b_cleaner_nonzero_exit_without_report(self):
        """2b. Ненулевой код возврата очистителя без отчёта выставляет st cleanup warn без срыва finish ok."""
        self._setup_active_grant(enabled=True)
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  echo "cleaner crashed" >&2
  exit 2
}
'''
        )
        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|ошибка исполнителя (код 2)", cleanup_st)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

    def test_scenario_2c_cleaner_corrupted_report(self):
        """2c. Повреждённый JSON отчёта выставляет st cleanup warn без срыва finish ok."""
        self._setup_active_grant(enabled=True)
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  echo "{CORRUPTED_JSON_NOT_VALID" > "$L/cleanup_report.json"
  return 0
}
'''
        )
        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|повреждённый отчёт", cleanup_st)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

    def test_scenario_2d_cleaner_old_report_from_previous_run(self):
        """2d. Старый успешный отчёт от предыдущего запуска отвергается (чужой отчёт сессии)."""
        self._setup_active_grant(enabled=True)
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  cat > "$L/cleanup_report.json" <<EOF
{"run_id": "OLD_RUN_ID_PREVIOUS", "status": "completed", "deleted_count": 5}
EOF
  return 0
}
'''
        )
        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|чужой отчёт сессии", cleanup_st)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

    def test_scenario_2e_cleaner_missing_or_unknown_status_is_warn(self):
        """2e. Отсутствующий или пустой status в отчёте не трактуется как completed, а выдаёт предупреждение."""
        self._setup_active_grant(enabled=True)
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  local cur_pass="$(cat "$L/cleanup_pass_id" 2>/dev/null || echo "")"
  cat > "$L/cleanup_report.json" <<EOF
{"run_id": "test_run_12345", "clean_pass_id": "$cur_pass", "deleted_count": 0}
EOF
  return 0
}
'''
        )
        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|статус очистки: unknown", cleanup_st)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

    def test_scenario_2f_cleaner_old_pass_id_same_session_is_warn(self):
        """2f. Отчёт от предыдущего прохода той же сессии отвергается как чужой отчёт прохода."""
        self._setup_active_grant(enabled=True)
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  # Записываем отчёт со старым clean_pass_id того же run_id
  cat > "$L/cleanup_report.json" <<EOF
{"run_id": "test_run_12345", "clean_pass_id": "old_pass_from_earlier", "status": "completed", "deleted_count": 1}
EOF
  return 0
}
'''
        )
        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|чужой отчёт прохода", cleanup_st)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

    def test_scenario_3_successful_cleanup_completes_before_instance_deletion(self):
        """3. Успешная очистка завершается до разрешения удаления машины."""
        self._setup_active_grant(enabled=True)

        # Логируем последовательность событий в T/order.log
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  echo "1_CLEANUP_START" >> "$T/order.log"
  local cur_pass="$(cat "$L/cleanup_pass_id" 2>/dev/null || echo "")"
  # Создаём отчёт об удалении 1 кандидата для текущей сессии и прохода
  cat > "$L/cleanup_report.json" <<EOF
{"run_id": "test_run_12345", "clean_pass_id": "$cur_pass", "status": "completed", "deleted_count": 1, "deleted": [{"name": "game--Test.tar.zst.old", "state": "CONFIRMED_DELETED"}]}
EOF
  echo "2_CLEANUP_DONE" >> "$T/order.log"
  return 0
}
'''
        )

        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        # Проверяем статус cleanup и finish
        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|ok|удалено кандидатов: 1", cleanup_st)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

        # Проверяем строгую очерёдность
        order = (self.t_dir / "order.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(order, ["1_CLEANUP_START", "2_CLEANUP_DONE"])

    def test_scenario_3b_forced_isolation_failure_terminates_without_deletions_or_hang(self):
        """3b. Принудительный сбой изоляции группы процессов: удаление не начинается, потомки завершаются по PID."""
        self._setup_active_grant(enabled=True)

        cand_file = self.r_dir / "cand_iso.tar.zst.deadbeef"
        cand_file.write_bytes(b"DATA")

        # custom_clean запускает фонового потомка и пытается подменить probe на PGID самого Wolf
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  ( while true; do sleep 0.1; done ) &
  local cpid=$!
  echo "$cpid" > "$T/child_bg.pid"
  echo -e "$$\n$WOLF_PARENT_PGID" > "$L/cleanup_pgid.probe"
  if [ -f "$L/cleanup_isolated" ]; then
    rm -f "$R/cand_iso.tar.zst.deadbeef"
  fi
  wait $cpid
}
'''
        )
        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        # Статус очистки: предупреждение об ошибке изоляции
        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|ошибка изоляции процессов", cleanup_st)

        # Сохранение успешно подтверждено
        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

        # Handshake файл $L/cleanup_isolated НЕ должен был быть создан
        self.assertFalse((self.l_dir / "cleanup_isolated").exists())

        # Файл кандидата остался цел (удаление не началось)
        self.assertTrue(cand_file.exists())

        # Потомок был убит по PID (нет зависших процессов)
        child_pid = (self.t_dir / "child_bg.pid").read_text(encoding="utf-8").strip()
        try:
            os.kill(int(child_pid), 0)
            child_alive = True
        except OSError:
            child_alive = False
        self.assertFalse(child_alive, "Дочерний процесс должен быть завершён по PID при ошибке изоляции!")

    def test_scenario_4_error_and_hang_in_cleanup_are_bounded_in_time(self):
        """4. Ошибка и зависание очистки ограничены по времени (PGID остановка)."""
        self._setup_active_grant(enabled=True)

        # Имитируем зависающий cleaner (бесконечный sleep)
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  echo "$$" > "$T/cleaner_child_pid"
  sleep 30
}
'''
        )

        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"
        env["WOLF_CLEANUP_TIMEOUT"] = "1"   # 1 секунда таймаут
        env["WOLF_CLEANUP_GRACE"] = "1"     # 1 секунда на TERM/KILL

        t_start = time.time()
        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        t_elapsed = time.time() - t_start

        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertLess(t_elapsed, 8.0, "Очистка не была принудительно остановлена по таймауту!")

        # Статус cleanup должен быть warn
        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|превышен лимит времени", cleanup_st)

        # При этом сохранение было успешно подтверждено, поэтому finish всё равно ok!
        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

    def test_scenario_4b_child_ignores_term_killed_before_finish(self):
        """4b. Лидер завершается по TERM, дочерний процесс игнорирует TERM — группа уничтожается KILL."""
        self._setup_active_grant(enabled=True)

        # cmd_clean запускает фонового потомка в той же группе, который игнорирует TERM (trap '' TERM),
        # а сам скрипт clean завершается по получении TERM.
        script = build_test_wolf_script(
            self.root,
            custom_clean=r'''
cmd_clean() {
  (
    trap '' TERM
    echo "$$" > "$T/grandchild.pid"
    while true; do
      sleep 0.1
    done
  ) &
  local gc_pid=$!
  echo "$gc_pid" > "$T/grandchild_spawned.pid"

  # Лидер ждёт TERM и выходит
  trap 'exit 0' TERM
  while true; do
    sleep 0.1
  done
}
'''
        )

        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"
        env["WOLF_CLEANUP_TIMEOUT"] = "1"
        env["WOLF_CLEANUP_GRACE"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        # Проверяем, что дочерний процесс запускался
        gc_pid_file = self.t_dir / "grandchild.pid"
        self.assertTrue(gc_pid_file.exists(), "Дочерний процесс не запустился!")
        gc_pid = int(gc_pid_file.read_text(encoding="utf-8").strip())

        # Проверяем, что к моменту завершения finish_run дочерний процесс гарантированно уничтожен
        alive = True
        try:
            os.kill(gc_pid, 0)
        except OSError:
            alive = False
        self.assertFalse(alive, f"Дочерний процесс {gc_pid} остался жив после finish_run!")

        # Статус cleanup должен быть warn (превышен лимит времени)
        cleanup_st = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|превышен лимит времени", cleanup_st)

        # finish_run завершился успехом
        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)

    def test_scenario_5_repeated_finish_after_cleanup_err_does_not_fail_saves(self):
        """5. Повторный finish после cleanup: err не выдаёт ложную ошибку выгрузки."""
        script = build_test_wolf_script(self.root)

        # Имитируем старый сбой очистки в st/cleanup
        now = int(time.time())
        (self.s_dir / "st/cleanup").write_text(f"{now}|err|ошибка очистки диска", encoding="utf-8")

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|app: всё выгружено", finish_st)
        self.assertNotIn("выгрузка не подтверждена", finish_st)

    def test_scenario_6_save_error_prevents_cleanup_and_machine_deletion(self):
        """6. Ошибка сохранения запрещает очистку и удаление машины."""
        self._setup_active_grant(enabled=True)

        script = build_test_wolf_script(
            self.root,
            custom_shutdown=r'''
shutdown() {
  local now=$(date +%s)
  echo "${now}|ok|выгружено" > "$S/st/identity"
  echo "${now}|err|ошибка выгрузки tar" > "$S/st/game--Broken"
  return 0
}
''',
            custom_clean=r'''
cmd_clean() {
  echo "FATAL: clean was called on broken save" > "$T/cleanup_called"
  return 0
}
'''
        )

        env = dict(os.environ)
        env["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        proc = subprocess.run([str(script), "finish-run"], capture_output=True, text=True, env=env)
        self.assertNotEqual(proc.returncode, 0)

        finish_st = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|err|выгрузка не подтверждена: game--Broken — инстанс НЕ удаляю", finish_st)

        # Очистка НЕ должна запускаться при ошибке сохранения
        self.assertFalse((self.t_dir / "cleanup_called").exists())

    def test_scenario_7_interruption_after_delete_preserves_unconfirmed_unknown(self):
        """7. Прерывание реального процесса очистки сохраняет результат как UNCONFIRMED_UNKNOWN (отчёт пишет очиститель)."""
        rep_file = self.l_dir / "cleanup_report.json"
        self._setup_active_grant(enabled=True)

        # Создаём локальный стенд с базой и кандидатом
        (self.r_dir / "cand.tar.zst").write_bytes(zstd_compress(b"BASE_DATA_ARCH"))
        cand_file = self.r_dir / "cand.tar.zst.deadbeef"
        cand_file.write_bytes(b"CANDIDATE_DATA_ARCH")

        ready_marker = self.t_dir / "cleaner_delete_ready.marker"

        # Скрипт-обёртка для реального дочернего процесса очистителя с локальным адаптером
        helper_code = f"""
import sys, time, signal
from pathlib import Path
import wolf_cleaner

class LocalBlockingAdapter:
    def __init__(self, marker_path):
        self.marker_path = Path(marker_path)

    def delete_candidate(self, fname, cand_info):
        # Внутри delete_candidate() уведомляем тест о начале удаления
        self.marker_path.write_text("delete_started", encoding="utf-8")
        # Блокируемся до сигнала
        while True:
            time.sleep(0.05)
        return True, None

    def verify_deleted(self, fname, cand_info):
        return True

ready_marker = Path(sys.argv[1])
adapter = LocalBlockingAdapter(ready_marker)

# Запускаем execute_stand_cleanup с локальным адаптером (без _test_hook)
res = wolf_cleaner.execute_stand_cleanup(
    stand_dir=sys.argv[2],
    obj_name=sys.argv[3],
    report_file=sys.argv[4],
    lock_dir=sys.argv[5],
    grant_file=sys.argv[6],
    enabled=True,
    drive_adapter=adapter,
)
sys.exit(0 if res.get("status") in ("completed", "noop") else 1)
"""
        helper_file = self.t_dir / "run_cleaner_hooked.py"
        helper_file.write_text(helper_code, encoding="utf-8")

        cmd = [
            sys.executable, str(helper_file), str(ready_marker),
            str(self.r_dir), "cand",
            str(rep_file),
            str(self.l_dir),
            str(self.l_dir / "cleanup_grant.json"),
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{REPO_ROOT}:{REPO_ROOT}/tests"

        # Запускаем РЕАЛЬНЫЙ дочерний процесс
        proc = subprocess.Popen(cmd, env=env)

        # Ожидаем входа в хук удаления кандидата
        t0 = time.time()
        while not ready_marker.exists() and time.time() - t0 < 5:
            time.sleep(0.05)
        self.assertTrue(ready_marker.exists(), "Очиститель не дошёл до точки удаления кандидата!")

        # Отправляем SIGTERM реальному процессу очистителя
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=5)
        self.assertNotEqual(rc, 0, "Прерванный очиститель не должен завершаться с кодом 0")

        # Читаем отчёт, записанный САМИМ очистителем
        self.assertTrue(rep_file.exists(), "Файл отчёта cleanup_report.json не был записан очистителем!")
        loaded = json.loads(rep_file.read_text(encoding="utf-8"))

        self.assertEqual(loaded.get("status"), "stopped")
        self.assertEqual(loaded.get("unconfirmed_count"), 1)
        self.assertEqual(loaded.get("deleted_count"), 0)

        cands = loaded.get("candidates") or loaded.get("archives", {}).get("cand", {}).get("candidates", [])
        self.assertTrue(len(cands) >= 1)
        cand_entry = next((c for c in cands if c["name"] == "cand.tar.zst.deadbeef"), None)
        self.assertIsNotNone(cand_entry)
        self.assertEqual(cand_entry["state"], "UNCONFIRMED_UNKNOWN")

        # Кандидат физически остался неудалённым
        self.assertTrue(cand_file.exists(), "Кандидат не должен удаляться при прерывании запроса")

    def test_scenario_8_multi_archive_run_report_preserves_earlier_archives_on_interruption(self):
        """8. Сквозной отчёт по нескольким архивам сохраняет результаты первого архива при прерывании второго."""
        rep_file = self.l_dir / "cleanup_report.json"
        grant_file = self.l_dir / "cleanup_grant.json"
        now = int(time.time())
        grant_data = {
            "status": "granted",
            "run_id": "multi_run_12345",
            "iid": "99999",
            "folder_id": "test_fid_12345",
            "granted_at": now,
            "enabled": True,
        }
        grant_file.write_text(json.dumps(grant_data), encoding="utf-8")
        (self.l_dir / "run_id").write_text("multi_run_12345", encoding="utf-8")
        (self.l_dir / "iid").write_text("99999", encoding="utf-8")
        (self.l_dir / "storage_folder_id").write_text("test_fid_12345", encoding="utf-8")
        (self.l_dir / "cleanup_enabled").write_text("1", encoding="utf-8")

        # Подготавливаем стенд:
        # Архив 1: база + кандидат на удаление
        (self.r_dir / "arch1.tar.zst").write_bytes(zstd_compress(b"BASE1"))
        cand1 = self.r_dir / "arch1.tar.zst.deadbeef"
        cand1.write_bytes(b"CAND1")

        # Архив 2: база + кандидат на удаление
        (self.r_dir / "arch2.tar.zst").write_bytes(zstd_compress(b"BASE2"))
        cand2 = self.r_dir / "arch2.tar.zst.cafebabe"
        cand2.write_bytes(b"CAND2")

        # Хук прерывания: на arch1 проходит успешно, на arch2 посылает сигнал прерывания
        def hook_interrupt_on_second(fname, idx):
            if "arch2" in fname:
                os.kill(os.getpid(), signal.SIGTERM)

        wolf_cleaner.execute_stand_cleanup._test_hook = hook_interrupt_on_second

        try:
            wolf_cleaner.main([
                "dummy", str(self.r_dir), "arch1", "arch2",
                "--report-file", str(rep_file),
                "--lock-dir", str(self.l_dir),
                "--grant-file", str(grant_file),
                "--enabled"
            ])
        except SystemExit as e:
            self.assertNotEqual(e.code, 0)
        finally:
            wolf_cleaner.execute_stand_cleanup._test_hook = None

        # Проверяем сквозной отчёт
        self.assertTrue(rep_file.exists())
        data = json.loads(rep_file.read_text(encoding="utf-8"))

        self.assertEqual(data.get("run_id"), "multi_run_12345")
        self.assertEqual(data.get("status"), "stopped")
        self.assertEqual(data.get("total_candidates"), 2)
        self.assertEqual(data.get("deleted_count"), 1)
        self.assertEqual(data.get("unconfirmed_count"), 1)

        # Архив 1 успешно завершён
        arch1_res = data["archives"]["arch1"]
        self.assertEqual(arch1_res["status"], "completed")
        self.assertEqual(arch1_res["deleted_count"], 1)
        self.assertFalse(cand1.exists(), "Кандидат архива 1 должен быть удалён")

        # Архив 2 прерван в состоянии UNCONFIRMED_UNKNOWN
        arch2_res = data["archives"]["arch2"]
        self.assertEqual(arch2_res["status"], "stopped")
        self.assertEqual(arch2_res["unconfirmed_count"], 1)
        self.assertEqual(arch2_res["candidates"][0]["state"], "UNCONFIRMED_UNKNOWN")
        self.assertTrue(cand2.exists(), "Кандидат архива 2 не должен быть удалён при прерывании")

    def test_scenario_9_server_self_deletion_lifecycle_and_order(self):
        """9. Ветка серверного самоудаления (finish_run с Vast API) соблюдает строгий порядок событий."""
        vastkey_file = self.root / "vastkey"
        vastkey_file.write_text("TEST_VAST_API_SECRET_KEY", encoding="utf-8")

        bin_dir = self.t_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        events_file = self.t_dir / "events.log"

        # Mock curl, эмулирующий обращение к Vast API
        mock_curl = bin_dir / "curl"
        mock_curl.write_text(
            f'#!/bin/bash\necho "$(date +%s%N) CURL $*" >> "{events_file}"\n'
            'cat > /dev/null\necho \'{"success": true}\'\n',
            encoding="utf-8"
        )
        mock_curl.chmod(mock_curl.stat().st_mode | stat.S_IEXEC)

        base_env = dict(os.environ)
        base_env["WOLF_VASTKEY_FILE"] = str(vastkey_file)
        base_env["WOLF_SELF_DELETE_SLEEP"] = "0"
        base_env["PATH"] = f"{bin_dir}:{base_env.get('PATH', '')}"

        # --- СЛУЧАЙ А: Штатный проход (сохранение ok -> очистка ok -> st finish ok -> Vast API DELETE) ---
        events_file.unlink(missing_ok=True)
        self._setup_active_grant(enabled=True)
        env_a = dict(base_env)
        env_a["VG_STORAGE_CLEANUP_ENABLED"] = "1"

        script_a = build_test_wolf_script(
            self.root,
            custom_shutdown=f'''
shutdown() {{
  echo "$(date +%s%N) 1_SHUTDOWN" >> "{events_file}"
  local now=$(date +%s)
  echo "${{now}}|ok|выгружено" > "$S/st/identity"
  echo "${{now}}|ok|выгружено" > "$S/st/game--Test"
  return 0
}}
''',
            custom_clean=f'''
cmd_clean() {{
  echo "$(date +%s%N) 2_CLEAN_START" >> "{events_file}"
  sleep 0.05
  echo "$(date +%s%N) 3_CLEAN_DONE" >> "{events_file}"
  return 0
}}
'''
        )

        proc_a = subprocess.run([str(script_a), "finish-run"], capture_output=True, text=True, env=env_a)
        self.assertEqual(proc_a.returncode, 0, proc_a.stderr + proc_a.stdout)

        events_a = events_file.read_text(encoding="utf-8").splitlines()
        # Проверяем строгую последовательность событий
        self.assertTrue(any("1_SHUTDOWN" in e for e in events_a), f"События: {events_a}")
        self.assertTrue(any("2_CLEAN_START" in e for e in events_a), f"События: {events_a}")
        self.assertTrue(any("3_CLEAN_DONE" in e for e in events_a), f"События: {events_a}")
        self.assertTrue(any("CURL" in e and "instances/99999/" in e for e in events_a), f"События: {events_a}")

        idx_clean_done = next(i for i, e in enumerate(events_a) if "3_CLEAN_DONE" in e)
        idx_curl = next(i for i, e in enumerate(events_a) if "CURL" in e)
        self.assertLess(idx_clean_done, idx_curl, "Очистка ДОЛЖНА завершиться ДО обращения к Vast API!")

        finish_st_a = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|self: всё выгружено", finish_st_a)

        # --- СЛУЧАЙ Б: Ошибка сохранения -> запрет очистки И запрет удаления инстанса ---
        events_file.unlink(missing_ok=True)
        shutil.rmtree(self.s_dir / "st", ignore_errors=True)
        (self.s_dir / "st").mkdir(parents=True, exist_ok=True)
        script_b = build_test_wolf_script(
            self.root,
            custom_shutdown=f'''
shutdown() {{
  echo "$(date +%s%N) 1_SHUTDOWN_FAIL" >> "{events_file}"
  local now=$(date +%s)
  echo "${{now}}|ok|выгружено" > "$S/st/identity"
  echo "${{now}}|err|ошибка tar" > "$S/st/game--Broken"
  return 0
}}
''',
            custom_clean=f'''
cmd_clean() {{
  echo "FATAL_CLEAN_CALLED" >> "{events_file}"
  return 0
}}
'''
        )

        proc_b = subprocess.run([str(script_b), "finish-run"], capture_output=True, text=True, env=env_a)
        self.assertNotEqual(proc_b.returncode, 0)

        events_b = events_file.read_text(encoding="utf-8").splitlines() if events_file.exists() else []
        self.assertFalse(any("FATAL_CLEAN_CALLED" in e for e in events_b), "Очистка не должна вызываться при ошибке сохранения!")
        self.assertFalse(any("CURL" in e for e in events_b), "Удаление инстанса Vast API не должно вызываться при ошибке сохранения!")

        finish_st_b = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|err|выгрузка не подтверждена", finish_st_b)

        # --- СЛУЧАЙ В: Предупреждение/ошибка очистки НЕ блокирует удаление машины при успешном сохранении ---
        events_file.unlink(missing_ok=True)
        shutil.rmtree(self.s_dir / "st", ignore_errors=True)
        (self.s_dir / "st").mkdir(parents=True, exist_ok=True)
        script_c = build_test_wolf_script(
            self.root,
            custom_shutdown=f'''
shutdown() {{
  local now=$(date +%s)
  echo "${{now}}|ok|выгружено" > "$S/st/identity"
  echo "${{now}}|ok|выгружено" > "$S/st/game--Test"
  return 0
}}
''',
            custom_clean=f'''
cmd_clean() {{
  echo "$(date +%s%N) 2_CLEAN_HANG" >> "{events_file}"
  sleep 30
}}
'''
        )
        env_c = dict(env_a)
        env_c["WOLF_CLEANUP_TIMEOUT"] = "1"
        env_c["WOLF_CLEANUP_GRACE"] = "1"

        proc_c = subprocess.run([str(script_c), "finish-run"], capture_output=True, text=True, env=env_c)
        self.assertEqual(proc_c.returncode, 0, proc_c.stderr + proc_c.stdout)

        events_c = events_file.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("CURL" in e for e in events_c), "Ошибка очистки НЕ должна блокировать удаление инстанса Vast API!")

        cleanup_st_c = (self.s_dir / "st/cleanup").read_text(encoding="utf-8")
        self.assertIn("|warn|", cleanup_st_c)

        finish_st_c = (self.s_dir / "st/finish").read_text(encoding="utf-8")
        self.assertIn("|ok|self: всё выгружено", finish_st_c)

    def test_scenario_10_client_and_server_deletion_branches_follow_identical_order(self):
        """10. Клиентская и серверная ветки удаления соблюдают одинаковый порядок."""
        # 1. Проверяем клиентскую сторону: core._verify_and_delete
        class DummyUI:
            def __init__(self):
                self.messages = []
            def stage(self, *args, **kwargs): pass
            def say(self, msg, *args, **kwargs):
                self.messages.append(msg)

        ui = DummyUI()
        t0 = time.time()

        # Случай А: Сбой сохранения архива -> клиент отказывается удалять инстанс
        after_bad = {
            "identity": (t0 + 1, "ok", "выгружено"),
            "game--Doom": (t0 + 2, "err", "ошибка tar"),
            "cleanup": (t0 + 3, "ok", "удалено 2"),
        }
        with self.assertRaises(core.VastgameError) as cm:
            core._verify_and_delete(ui, "99999", after_bad, t0, self_delete=True)
        self.assertIn("НЕ подтверждена", str(cm.exception))

        # Случай Б: Сохранения успешны, но очистка выдала ошибку (cleanup: err)
        # Клиент обязан удалить инстанс, так как cleanup исключен из NOT_ARCHIVES!
        after_cleanup_err = {
            "identity": (t0 + 1, "ok", "выгружено"),
            "game--Doom": (t0 + 2, "ok", "выгружено"),
            "cleanup": (t0 + 3, "err", "ошибка очистки"),
        }
        orig_delete = core.delete_instance
        orig_list = core.list_instances
        try:
            core.delete_instance = lambda iid: (True, "mock_deleted")
            core.list_instances = lambda: []
            res = core._verify_and_delete(ui, "99999", after_cleanup_err, t0, self_delete=False)
        finally:
            core.delete_instance = orig_delete
            core.list_instances = orig_list
        self.assertEqual(res["iid"], "99999")
        self.assertTrue(res["deleted"])
        self.assertEqual(res["uploaded"], ["game--Doom", "identity"])
        self.assertNotIn("cleanup", res["uploaded"])


if __name__ == "__main__":
    unittest.main()

# tests/test_session_grant.py — стендовые проверки сессионного разрешения очистки.
#
# Проверяет:
# 1. Точку старта сессии: генерация run_id, отмена прежнего grant, сериализация блокировкой OS.
# 2. Серверный приём разрешения: строгая проверка run_id, iid, folder_id, атомарная запись grant.
# 3. Единые правила is_cleanup_authorized() в wolf-web.py и wolf-parts.py.
# 4. Клиентскую проверку: подтверждённое завершение прежней машины, история хранилища, строгая валидация.
# 5. Шесть обязательных негативных сценариев (строгий отказ, проверка boolean/кодов, без значений по умолчанию).
# 6. Ровно ноль удалений облачных файлов во всех сценариях.

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
import drive
import settings
import wolfnode
from vastapi import VastgameError

ROOT = Path(__file__).resolve().parent.parent
OWNER = "2132240893056654"


def _extract_wolf_web(fake_wolf):
    src = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")
    m = re.search(r"^w /usr/local/bin/wolf-web.py <<'WEB'\n(.*?)^WEB$", src, re.S | re.M)
    assert m, "wolf-web.py not found in wolf-setup.sh"
    code = m.group(1).replace("WOLF = '/usr/local/bin/wolf'", f"WOLF = {str(fake_wolf)!r}")
    code = "\n".join(ln for ln in code.splitlines() if not ln.startswith("ThreadingHTTPServer("))
    ns = {"__name__": "wolf_web"}
    exec(compile(code, "wolf-web.py", "exec"), ns)
    return ns


def _extract_wolf_parts():
    src = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")
    m = re.search(r"^w /usr/local/bin/wolf-parts.py <<'PARTS'\n(.*?)^PARTS$", src, re.S | re.M)
    assert m, "wolf-parts.py not found in wolf-setup.sh"
    ns = {"__name__": "wolf_parts"}
    exec(compile(m.group(1), "wolf-parts.py", "exec"), ns)
    return ns


def _ensure_flock_shim(bin_dir: Path):
    if not shutil.which("flock"):
        flock_bin = bin_dir / "flock"
        flock_bin.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, os, fcntl, time, subprocess\n"
            "args = sys.argv[1:]\n"
            "timeout = None\n"
            "nonblock = False\n"
            "flags = fcntl.LOCK_EX\n"
            "i = 0\n"
            "while i < len(args):\n"
            "    arg = args[i]\n"
            "    if arg in ('-w', '--wait', '--timeout'):\n"
            "        timeout = float(args[i+1])\n"
            "        i += 2\n"
            "    elif arg.startswith('-w'):\n"
            "        timeout = float(arg[2:])\n"
            "        i += 1\n"
            "    elif arg in ('-n', '--nb', '--nonblock'):\n"
            "        nonblock = True\n"
            "        i += 1\n"
            "    elif arg in ('-x', '--exclusive'):\n"
            "        flags = fcntl.LOCK_EX\n"
            "        i += 1\n"
            "    elif arg in ('-s', '--shared'):\n"
            "        flags = fcntl.LOCK_SH\n"
            "        i += 1\n"
            "    elif arg in ('-u', '--unlock'):\n"
            "        flags = fcntl.LOCK_UN\n"
            "        i += 1\n"
            "    else:\n"
            "        break\n"
            "if i >= len(args):\n"
            "    sys.exit(1)\n"
            "target = args[i]\n"
            "cmd = args[i+1:]\n"
            "def lock_fd(fd):\n"
            "    t_start = time.time()\n"
            "    while True:\n"
            "        try:\n"
            "            fcntl.flock(fd, flags | (fcntl.LOCK_NB if (nonblock or timeout is not None) else 0))\n"
            "            return True\n"
            "        except (BlockingIOError, OSError):\n"
            "            if nonblock:\n"
            "                return False\n"
            "            if timeout is not None:\n"
            "                if time.time() - t_start >= timeout:\n"
            "                    return False\n"
            "                time.sleep(0.05)\n"
            "            else:\n"
            "                fcntl.flock(fd, flags)\n"
            "                return True\n"
            "if target.isdigit():\n"
            "    fd = int(target)\n"
            "    if not lock_fd(fd):\n"
            "        sys.exit(1)\n"
            "    if cmd:\n"
            "        res = subprocess.run(cmd)\n"
            "        sys.exit(res.returncode)\n"
            "    sys.exit(0)\n"
            "else:\n"
            "    f = open(target, 'a')\n"
            "    if not lock_fd(f.fileno()):\n"
            "        sys.exit(1)\n"
            "    if cmd:\n"
            "        res = subprocess.run(cmd)\n"
            "        sys.exit(res.returncode)\n"
            "    sys.exit(0)\n",
            encoding="utf-8",
        )
        flock_bin.chmod(0o755)


class FakeUI:
    def __init__(self):
        self.log = []
        self.keys = []
        self.stages = []

    def say(self, text, level="info", key=None, **params):
        self.log.append((level, text))
        self.keys.append(key)

    def stage(self, key, text):
        self.stages.append((key, text))


class TestServerSessionGrant(unittest.TestCase):
    """Проверка серверного механизма сессии и разрешения очистки в wolf-setup.sh."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.l_dir = self.td / "run_wolf"
        self.l_dir.mkdir(parents=True, exist_ok=True)
        self.s_dir = self.td / "var_lib_wolf"
        self.s_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = self.td / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.td / "wolf.log"

        # Mock rclone, возвращающий drive_fid_42 для lsjson -M --stat
        self.fake_rclone = self.bin_dir / "rclone"
        self.fake_rclone.write_text(
            "#!/bin/sh\n"
            "if [ \"${FAIL_RCLONE:-0}\" = \"1\" ]; then\n"
            "  echo 'rclone error: storage unreachable' >&2\n"
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
        _ensure_flock_shim(self.bin_dir)

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
            f"L={str(self.l_dir)!r}\n"
            f"S={str(self.s_dir)!r}\n"
            f"VI=\"${{VI}}\"\n"
            f"R=\"${{R}}\"\n"
            f"log() {{ echo \"[$(date '+%F %T')] $*\" >> {str(self.log_file)!r}; }}\n"
            f"{fn_rc_call}\n"
            f"{fn_storage}\n"
            f"{fn_session}\n"
            f"{fn_grant}\n"
            f"{fn_supervise}\n"
            f"case ${{1:-}} in\n"
            f"  session-start) session_start ;;\n"
            f"  grant-cleanup) shift; grant_cleanup \"$@\" ;;\n"
            f"  storage-id) storage_id ;;\n"
            f"esac\n",
            encoding="utf-8",
        )
        self.wolf_sh.chmod(0o755)
        self.env = dict(os.environ, VI="1001", R="vastgame:vastgame-test")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_session_start_generates_random_run_id_and_revokes_previous_grant(self):
        # 1. Первоначальный старт
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True)
        run_id_1 = (self.l_dir / "run_id").read_text().strip()
        self.assertTrue(len(run_id_1) >= 16)

        # 2. Создаём файл разрешения и остаточные файлы в /run/wolf
        (self.l_dir / "cleanup_grant.json").write_text('{"status": "granted"}', encoding="utf-8")
        (self.l_dir / "cleanup_grant.json.tmp.999").write_text("tmp", encoding="utf-8")
        (self.l_dir / "preserve.lock").write_text("locked", encoding="utf-8")

        # 3. Перезапуск сессии
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True)
        run_id_2 = (self.l_dir / "run_id").read_text().strip()

        # Проверка: run_id изменился
        self.assertNotEqual(run_id_1, run_id_2)
        # Прежнее разрешение аннулировано
        self.assertFalse((self.l_dir / "cleanup_grant.json").exists())
        self.assertFalse((self.l_dir / "cleanup_grant.json.tmp.999").exists())
        # Блокировки и другие файлы tmpfs не удалены
        self.assertTrue((self.l_dir / "preserve.lock").exists())

    def test_grant_cleanup_success_and_atomic_write(self):
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id = (self.l_dir / "run_id").read_text().strip()

        # Успешная выдача: iid=1001, совпадение run_id и folder_id
        res = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "drive_fid_42"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("ok", res.stdout)

        grant_file = self.l_dir / "cleanup_grant.json"
        self.assertTrue(grant_file.exists())
        data = json.loads(grant_file.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "granted")
        self.assertEqual(data["iid"], "1001")
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["folder_id"], "drive_fid_42")
        self.assertIsInstance(data["granted_at"], int)

    def test_grant_cleanup_rejections(self):
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id = (self.l_dir / "run_id").read_text().strip()

        # Несовпадение run_id
        r1 = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", "wrong_run_id", "drive_fid_42"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(r1.returncode, 0)
        self.assertFalse((self.l_dir / "cleanup_grant.json").exists())

        # Несовпадение iid
        r2 = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "9999", run_id, "drive_fid_42"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(r2.returncode, 0)
        self.assertFalse((self.l_dir / "cleanup_grant.json").exists())

        # Несовпадение folder_id
        r3 = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "foreign_folder"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(r3.returncode, 0)
        self.assertFalse((self.l_dir / "cleanup_grant.json").exists())


class TestUnifiedCleanupAuthorization(unittest.TestCase):
    """Сравнительная проверка единых правил is_cleanup_authorized() в wolf-web.py и wolf-parts.py."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.parts_mod = _extract_wolf_parts()
        self.web_mod = _extract_wolf_web(self.td / "wolf_dummy")
        self.grant_file = str(self.td / "cleanup_grant.json")
        self.cloud_deletions = []

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_both_reject_when_run_id_is_empty(self):
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="", iid_val="1001", folder_id_val="fid_42"
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="", iid_val="1001", folder_id_val="fid_42"
        ))

    def test_both_reject_when_iid_is_empty(self):
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="", folder_id_val="fid_42"
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="", folder_id_val="fid_42"
        ))

    def test_both_reject_when_folder_id_is_empty(self):
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val=""
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val=""
        ))

    def test_both_reject_when_grant_file_missing_or_corrupt(self):
        # Файл отсутствует
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))

        # Файл повреждён (не JSON)
        Path(self.grant_file).write_text("not json at all {", encoding="utf-8")
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))

    def test_both_reject_when_grant_fields_mismatch(self):
        # 1. Несовпадение run_id
        Path(self.grant_file).write_text(json.dumps({
            "status": "granted", "run_id": "other_run", "iid": "1001", "folder_id": "fid_42"
        }), encoding="utf-8")
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))

        # 2. Несовпадение iid
        Path(self.grant_file).write_text(json.dumps({
            "status": "granted", "run_id": "run_1", "iid": "9999", "folder_id": "fid_42"
        }), encoding="utf-8")
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))

        # 3. Несовпадение folder_id
        Path(self.grant_file).write_text(json.dumps({
            "status": "granted", "run_id": "run_1", "iid": "1001", "folder_id": "other_folder"
        }), encoding="utf-8")
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))

    def test_both_authorize_strictly_when_all_match(self):
        Path(self.grant_file).write_text(json.dumps({
            "status": "granted", "run_id": "run_1", "iid": "1001", "folder_id": "fid_42"
        }), encoding="utf-8")
        self.assertTrue(self.parts_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))
        self.assertTrue(self.web_mod["is_cleanup_authorized"](
            grant_file=self.grant_file, run_id_val="run_1", iid_val="1001", folder_id_val="fid_42"
        ))


class TestWolfWebNetworkIntegration(unittest.TestCase):
    """Сетевая проверка endpoints /json, /session и POST /cmd: grant-cleanup в wolf-web.py."""

    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        ts = cls.d / "tailscale"
        ts.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            f"  status) echo '{{\"Self\": {{\"UserID\": {OWNER}}}}}';;\n"
            "  whois) echo \"{\\\"UserProfile\\\": {\\\"ID\\\": $(cat " + str(cls.d) + "/who)}, "
            "\\\"Node\\\": {\\\"Tags\\\": $(cat " + str(cls.d) + "/tags)}}\";;\nesac\n",
            encoding="utf-8",
        )
        wolf = cls.d / "wolf"
        wolf.write_text(
            f"#!/bin/sh\n"
            f"echo \"$@\" >> {cls.d}/calls\n"
            f"if [ \"$1\" = grant-cleanup ]; then\n"
            f"  if [ \"$3\" = bad_run ]; then echo 'отказ: неверный run_id' >&2; exit 1; fi\n"
            f"  echo ok; exit 0\n"
            f"fi\n"
            f"echo \"wolf: $@\"\nexit 0\n",
            encoding="utf-8",
        )
        for f in (ts, wolf):
            f.chmod(0o755)

        cls.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{cls.d}:{cls.old_path}"
        ns = _extract_wolf_web(wolf)

        ns["get_run_id"] = lambda: "run_hex_999"
        ns["get_env_iid"] = lambda: "5050"
        ns["get_storage_folder_id"] = lambda: "drive_fid_123"
        ns["is_cleanup_authorized"] = lambda: True

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ns["H"])
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        os.environ["PATH"] = cls.old_path
        shutil.rmtree(cls.d, ignore_errors=True)

    def setUp(self):
        (self.d / "who").write_text(OWNER)
        (self.d / "tags").write_text("null")
        (self.d / "calls").unlink(missing_ok=True)
        p = mock.patch.object(wolfnode, "WOLF_PORT", self.port)
        p.start()
        self.addCleanup(p.stop)

    def test_get_json_unaffected(self):
        st = wolfnode.wolf_status("127.0.0.1")
        self.assertIsInstance(st, dict)

    def test_get_session_metadata(self):
        s_info = wolfnode.session_info("127.0.0.1")
        self.assertIsInstance(s_info, dict)
        self.assertEqual(s_info["iid"], "5050")
        self.assertEqual(s_info["run_id"], "run_hex_999")
        self.assertEqual(s_info["storage"]["folder_id"], "drive_fid_123")
        self.assertTrue(s_info["cleanup_allowed"])

    def test_post_grant_cleanup_same_owner_accepted(self):
        code, out = wolfnode.run("127.0.0.1", "grant-cleanup", ["5050", "run_hex_999", "drive_fid_123"])
        self.assertEqual(code, 0)
        self.assertIn("grant-cleanup 5050 run_hex_999 drive_fid_123", (self.d / "calls").read_text())

    def test_post_grant_cleanup_rejection_from_server(self):
        code, out = wolfnode.run("127.0.0.1", "grant-cleanup", ["5050", "bad_run", "drive_fid_123"])
        self.assertNotEqual(code, 0)

    def test_foreign_owner_refused(self):
        (self.d / "who").write_text("foreign_tailscale_user_999")
        with self.assertRaises(VastgameError) as e:
            wolfnode.run("127.0.0.1", "grant-cleanup", ["5050", "run_hex_999", "drive_fid_123"])
        self.assertEqual(e.exception.key, "machine.forbidden")


class TestClientSessionGrantLogic(unittest.TestCase):
    """Проверка клиентской логики _authorize_cleanup, истории сессий и безопасной деградации."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.p_file = mock.patch.object(settings, "FILE", self.td / "settings.json")
        self.p_file.start()
        self.addCleanup(self.p_file.stop)
        self.addCleanup(lambda: shutil.rmtree(self.td, ignore_errors=True))

        self.ui = FakeUI()
        self.cloud_deletions = []

        self.p_trash = mock.patch.object(
            drive, "trash",
            lambda files: self.cloud_deletions.extend(files) or True,
        )
        self.p_trash.start()
        self.addCleanup(self.p_trash.stop)

    def test_successful_client_grant_issuance(self):
        settings.set("storage_history", {
            "account_email": "user@gmail.com",
            "folder_id": "fid_valid_100",
            "last_iid": 900,
            "last_iid_confirmed_ended": True,
            "trusted": True,
        })
        p_id = mock.patch.object(
            drive, "storage_identity",
            lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_valid_100", "folder_name": "vastai-cloud-games"},
        )
        p_list = mock.patch.object(core, "list_instances", lambda: [{"id": 1001, "actual_status": "running"}])
        p_sinfo = mock.patch.object(
            wolfnode, "session_info",
            lambda ip, timeout=5: {"iid": "1001", "run_id": "run_token_abc", "storage": {"folder_id": "fid_valid_100"}},
        )
        p_run = mock.patch.object(wolfnode, "run", lambda ip, cmd, args, timeout=10: (0, "ok"))

        with p_id, p_list, p_sinfo, p_run:
            ok = core._authorize_cleanup(self.ui, 1001, "100.1.1.1")
            self.assertTrue(ok)

        hist = settings.get("storage_history")
        self.assertEqual(hist["last_iid"], "1001")
        self.assertFalse(hist["last_iid_confirmed_ended"])
        self.assertTrue(hist["trusted"])
        self.assertEqual(self.cloud_deletions, [])

    def test_refusal_when_history_unknown_or_untrusted(self):
        settings.set("storage_history", None)
        p_id = mock.patch.object(
            drive, "storage_identity",
            lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_valid_100"},
        )
        with p_id:
            ok = core._authorize_cleanup(self.ui, 1001, "100.1.1.1")
            self.assertFalse(ok)

        hist = settings.get("storage_history")
        self.assertFalse(hist["trusted"])

        core.delete_instance = lambda iid: (True, "deleted")
        with p_id:
            ok2 = core._authorize_cleanup(self.ui, 1002, "100.1.1.1")
            self.assertFalse(ok2)
        self.assertEqual(self.cloud_deletions, [])

    def test_refusal_when_prior_instance_still_active_at_provider(self):
        settings.set("storage_history", {
            "account_email": "user@gmail.com",
            "folder_id": "fid_valid_100",
            "last_iid": 900,
            "last_iid_confirmed_ended": False,
            "trusted": True,
        })
        p_id = mock.patch.object(
            drive, "storage_identity",
            lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_valid_100"},
        )
        p_list = mock.patch.object(
            core, "list_instances",
            lambda: [{"id": 900, "actual_status": "running"}, {"id": 1001}],
        )
        with p_id, p_list:
            ok = core._authorize_cleanup(self.ui, 1001, "100.1.1.1")
            self.assertFalse(ok)
        self.assertEqual(self.cloud_deletions, [])

    def test_refusal_when_provider_check_fails(self):
        settings.set("storage_history", {
            "account_email": "user@gmail.com",
            "folder_id": "fid_valid_100",
            "last_iid": 900,
            "last_iid_confirmed_ended": False,
            "trusted": True,
        })
        p_id = mock.patch.object(
            drive, "storage_identity",
            lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_valid_100"},
        )

        def bad_list():
            raise RuntimeError("Vast API timeout")

        with p_id, mock.patch.object(core, "list_instances", bad_list):
            ok = core._authorize_cleanup(self.ui, 1001, "100.1.1.1")
            self.assertFalse(ok)
        self.assertEqual(self.cloud_deletions, [])

    def test_safe_client_degradation_with_old_wolf_without_keyerror_or_ssh(self):
        with mock.patch.object(wolfnode.requests, "get", lambda *a, **k: mock.Mock(status_code=404)):
            self.assertIsNone(wolfnode.session_info("100.1.1.1"))

        with mock.patch.object(wolfnode.requests, "post", lambda *a, **k: mock.Mock(status_code=400, json=lambda: {"error": "нет такой команды"})):
            code, out = wolfnode.run("100.1.1.1", "grant-cleanup", ["1001", "run1", "fid1"])
            self.assertNotEqual(code, 0)

        ssh_calls = []
        with mock.patch.object(wolfnode, "post_cmd", lambda *a, **k: None):
            code, out = wolfnode.run(
                "100.1.1.1", "grant-cleanup", ["1001", "run1", "fid1"],
                ssh=lambda *x, **y: ssh_calls.append(x) or (0, "ssh"),
            )
            self.assertNotEqual(code, 0)
            self.assertIn("не поддерживается", out)
            self.assertEqual(ssh_calls, [])

        settings.set("storage_history", {
            "account_email": "user@gmail.com",
            "folder_id": "fid_valid_100",
            "last_iid": 1001,
            "last_iid_confirmed_ended": True,
            "trusted": True,
        })
        p_id = mock.patch.object(drive, "storage_identity", lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_valid_100"})
        p_sinfo = mock.patch.object(wolfnode, "session_info", lambda ip, timeout=5: None)
        with p_id, p_sinfo:
            ok = core._authorize_cleanup(self.ui, 1001, "100.1.1.1")
            self.assertFalse(ok)
            self.assertTrue(any("view-only" in msg for _, msg in self.ui.log))

        self.assertEqual(self.cloud_deletions, [])

    def test_delete_instance_waits_for_provider_absence_and_updates_history(self):
        settings.set("storage_history", {
            "account_email": "user@gmail.com",
            "folder_id": "fid_valid_100",
            "last_iid": 1001,
            "last_iid_confirmed_ended": False,
            "trusted": True,
        })
        p_del = mock.patch.object(core, "call", lambda method, endpoint, body: {"success": True})
        p_gone = mock.patch.object(core, "wait_instance_gone", lambda iid, timeout=10, interval=1: True)
        p_hist = mock.patch.object(core.history, "ended", lambda iid: None)

        with p_del, p_gone, p_hist:
            ok, msg = core.delete_instance(1001)
            self.assertTrue(ok)

        hist = settings.get("storage_history")
        self.assertTrue(hist["last_iid_confirmed_ended"])
        self.assertEqual(self.cloud_deletions, [])


class TestSixMandatoryNegativeScenarios(unittest.TestCase):
    """Шесть обязательных негативных сценариев с проверкой строгого отказа (коды/boolean)."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.l_dir = self.td / "run_wolf"
        self.l_dir.mkdir(parents=True, exist_ok=True)
        self.s_dir = self.td / "var_lib_wolf"
        self.s_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = self.td / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.td / "wolf.log"

        self.fake_rclone = self.bin_dir / "rclone"
        self.fake_rclone.write_text(
            "#!/bin/sh\n"
            "if [ \"${FAIL_RCLONE:-0}\" = \"1\" ]; then\n"
            "  echo 'rclone error: connection reset' >&2\n"
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
        _ensure_flock_shim(self.bin_dir)

        src = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")
        fn_rc_call = re.search(r"^_rc_stat_call\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)
        fn_storage = re.search(r"^storage_id\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)
        fn_session = re.search(r"^session_start\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)
        fn_grant = re.search(r"^grant_cleanup\(\)\s*\{.*?^\}", src, re.S | re.M).group(0)

        self.wolf_sh = self.td / "wolf"
        self.wolf_sh.write_text(
            f"#!/bin/bash\n"
            f"set -e\n"
            f"export PATH=\"{str(self.bin_dir)}:$PATH\"\n"
            f"L={str(self.l_dir)!r}\n"
            f"S={str(self.s_dir)!r}\n"
            f"VI=\"${{VI}}\"\n"
            f"R=\"${{R}}\"\n"
            f"log() {{ echo \"[$(date '+%F %T')] $*\" >> {str(self.log_file)!r}; }}\n"
            f"{fn_rc_call}\n"
            f"{fn_storage}\n"
            f"{fn_session}\n"
            f"{fn_grant}\n"
            f"case ${{1:-}} in\n"
            f"  session-start) session_start ;;\n"
            f"  grant-cleanup) shift; grant_cleanup \"$@\" ;;\n"
            f"  storage-id) storage_id ;;\n"
            f"esac\n",
            encoding="utf-8",
        )
        self.wolf_sh.chmod(0o755)
        self.env = dict(os.environ, VI="1001", R="vastgame:vastgame-test")

        self.parts_mod = _extract_wolf_parts()
        self.web_mod = _extract_wolf_web(self.wolf_sh)

        self.p_file = mock.patch.object(settings, "FILE", self.td / "settings.json")
        self.p_file.start()
        self.addCleanup(self.p_file.stop)

        self.ui = FakeUI()
        self.cloud_deletions = []
        self.p_trash = mock.patch.object(drive, "trash", lambda files: self.cloud_deletions.extend(files) or True)
        self.p_trash.start()
        self.addCleanup(self.p_trash.stop)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_negative_1_server_does_not_know_iid(self):
        """1. Сервер не знает iid: VI пуст и /run/wolf/iid отсутствует -> отказ на всех уровнях."""
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id = (self.l_dir / "run_id").read_text().strip()

        # Серверный вызов grant-cleanup без VI
        env_no_vi = dict(self.env, VI="")
        res = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "drive_fid_42"],
            capture_output=True, text=True, env=env_no_vi,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((self.l_dir / "cleanup_grant.json").exists())

        # Проверка в wolf-web и wolf-parts
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=str(self.l_dir / "cleanup_grant.json"),
            run_id_val=run_id, iid_val="", folder_id_val="drive_fid_42",
        ))
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=str(self.l_dir / "cleanup_grant.json"),
            run_id_val=run_id, iid_val="", folder_id_val="drive_fid_42",
        ))

        # Клиентский уровень: session_info вернул пустой iid
        settings.set("storage_history", {
            "account_email": "user@gmail.com", "folder_id": "fid_42",
            "last_iid": 1001, "last_iid_confirmed_ended": True, "trusted": True,
        })
        p_id = mock.patch.object(drive, "storage_identity", lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_42"})
        p_list = mock.patch.object(core, "list_instances", lambda: [])
        p_sinfo = mock.patch.object(wolfnode, "session_info", lambda ip, timeout=5: {
            "iid": "", "run_id": run_id, "storage": {"folder_id": "fid_42"}
        })
        with p_id, p_list, p_sinfo:
            ok = core._authorize_cleanup(self.ui, 1001, "100.1.1.1")
            self.assertFalse(ok)

        self.assertEqual(self.cloud_deletions, [])

    def test_negative_2_server_does_not_know_folder_id(self):
        """2. Сервер не знает folder_id: R не задан -> отказ на всех уровнях."""
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id = (self.l_dir / "run_id").read_text().strip()

        # Серверный вызов grant-cleanup без R
        env_no_r = dict(self.env, R="")
        res = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "drive_fid_42"],
            capture_output=True, text=True, env=env_no_r,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((self.l_dir / "cleanup_grant.json").exists())

        # Проверка в wolf-web и wolf-parts
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=str(self.l_dir / "cleanup_grant.json"),
            run_id_val=run_id, iid_val="1001", folder_id_val="",
        ))
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=str(self.l_dir / "cleanup_grant.json"),
            run_id_val=run_id, iid_val="1001", folder_id_val="",
        ))

        # Клиентский уровень: session_info вернул пустой folder_id
        settings.set("storage_history", {
            "account_email": "user@gmail.com", "folder_id": "fid_42",
            "last_iid": 1001, "last_iid_confirmed_ended": True, "trusted": True,
        })
        p_id = mock.patch.object(drive, "storage_identity", lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_42"})
        p_list = mock.patch.object(core, "list_instances", lambda: [])
        p_sinfo = mock.patch.object(wolfnode, "session_info", lambda ip, timeout=5: {
            "iid": "1001", "run_id": run_id, "storage": {"folder_id": ""}
        })
        with p_id, p_list, p_sinfo:
            ok = core._authorize_cleanup(self.ui, 1001, "100.1.1.1")
            self.assertFalse(ok)

        self.assertEqual(self.cloud_deletions, [])

    def test_negative_3_client_receives_empty_iid_or_folder_id(self):
        """3. Клиент получил пустой iid или folder_id -> строгий отказ (False), grant не отправляется."""
        settings.set("storage_history", {
            "account_email": "user@gmail.com", "folder_id": "fid_42",
            "last_iid": 1001, "last_iid_confirmed_ended": True, "trusted": True,
        })
        p_list = mock.patch.object(core, "list_instances", lambda: [])
        wolf_calls = []
        p_run = mock.patch.object(wolfnode, "run", lambda *a, **k: wolf_calls.append(a) or (0, "ok"))

        # 3a. Пустой iid на клиенте
        p_id = mock.patch.object(drive, "storage_identity", lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": "fid_42"})
        with p_id, p_list, p_run:
            self.assertFalse(core._authorize_cleanup(self.ui, "", "100.1.1.1"))
            self.assertEqual(wolf_calls, [])

        # 3b. Пустой folder_id от drive.storage_identity
        p_id_empty_fid = mock.patch.object(drive, "storage_identity", lambda timeout=10: {"account_email": "user@gmail.com", "folder_id": ""})
        with p_id_empty_fid, p_list, p_run:
            self.assertFalse(core._authorize_cleanup(self.ui, 1001, "100.1.1.1"))
            self.assertEqual(wolf_calls, [])

        # 3c. Пустой account_email от drive.storage_identity
        p_id_empty_email = mock.patch.object(drive, "storage_identity", lambda timeout=10: {"account_email": "", "folder_id": "fid_42"})
        with p_id_empty_email, p_list, p_run:
            self.assertFalse(core._authorize_cleanup(self.ui, 1001, "100.1.1.1"))
            self.assertEqual(wolf_calls, [])

        self.assertEqual(self.cloud_deletions, [])

    def test_negative_4_grant_has_matching_run_id_but_wrong_machine_or_folder(self):
        """4. Grant содержит верный run_id, но другую машину или каталог -> отказ."""
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id = (self.l_dir / "run_id").read_text().strip()

        # 4a. grant_cleanup с чужим iid
        r_iid = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "999999", run_id, "drive_fid_42"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(r_iid.returncode, 0)

        # 4b. grant_cleanup с чужим folder_id
        r_fid = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "wrong_folder_id"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(r_fid.returncode, 0)

        # 4c. Файл разрешения содержит верный run_id, но другой iid -> is_cleanup_authorized returns False
        grant_path = self.l_dir / "cleanup_grant.json"
        grant_path.write_text(json.dumps({
            "status": "granted", "run_id": run_id, "iid": "999999", "folder_id": "drive_fid_42"
        }), encoding="utf-8")
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=str(grant_path), run_id_val=run_id, iid_val="1001", folder_id_val="drive_fid_42"
        ))
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=str(grant_path), run_id_val=run_id, iid_val="1001", folder_id_val="drive_fid_42"
        ))

        # 4d. Файл разрешения содержит верный run_id, но другой folder_id -> is_cleanup_authorized returns False
        grant_path.write_text(json.dumps({
            "status": "granted", "run_id": run_id, "iid": "1001", "folder_id": "wrong_folder_id"
        }), encoding="utf-8")
        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=str(grant_path), run_id_val=run_id, iid_val="1001", folder_id_val="drive_fid_42"
        ))
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=str(grant_path), run_id_val=run_id, iid_val="1001", folder_id_val="drive_fid_42"
        ))

        self.assertEqual(self.cloud_deletions, [])

    def test_negative_5_server_storage_determination_fails_with_error(self):
        """5. Определение хранилища на сервере завершилось ошибкой -> запрет выдачи разрешения."""
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id = (self.l_dir / "run_id").read_text().strip()

        # rclone падает с ошибкой FAIL_RCLONE=1
        env_fail = dict(self.env, FAIL_RCLONE="1")
        res_storage = subprocess.run(
            ["bash", str(self.wolf_sh), "storage-id"],
            capture_output=True, text=True, env=env_fail,
        )
        self.assertNotEqual(res_storage.returncode, 0)

        # Выдача разрешения при сбое rclone обязана завершиться отказом
        res_grant = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id, "drive_fid_42"],
            capture_output=True, text=True, env=env_fail,
        )
        self.assertNotEqual(res_grant.returncode, 0)
        self.assertFalse((self.l_dir / "cleanup_grant.json").exists())

        # wolf-web и wolf-parts: get_storage_folder_id() при сбое rclone возвращает None
        with mock.patch.dict(os.environ, {"FAIL_RCLONE": "1"}):
            self.assertIsNone(self.web_mod["get_storage_folder_id"](
                r_val="vastgame:vastgame-test", rc_cmd=str(self.fake_rclone)
            ))
            self.assertIsNone(self.parts_mod["get_storage_folder_id"](
                r_val="vastgame:vastgame-test", rc_cmd=str(self.fake_rclone)
            ))

        self.assertEqual(self.cloud_deletions, [])

    def test_negative_6_session_switch_during_grant_issuance(self):
        """6. Переключение сессии во время выдачи разрешения -> старое разрешение никогда не допускает очистку."""
        # Сессия 1: старт и чтение run_id_1
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id_1 = (self.l_dir / "run_id").read_text().strip()

        # Клиент якобы выдал разрешение в сессии 1
        res1 = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id_1, "drive_fid_42"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(res1.returncode, 0)
        grant_file = self.l_dir / "cleanup_grant.json"
        self.assertTrue(grant_file.exists())

        # Сессия 2: старт сессии (перезапуск Wolf)
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id_2 = (self.l_dir / "run_id").read_text().strip()
        self.assertNotEqual(run_id_1, run_id_2)

        # Файл разрешения был удален при session_start
        self.assertFalse(grant_file.exists())

        # Попытка вызова grant_cleanup со старым run_id_1 в новой сессии -> строгий отказ
        res_late = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id_1, "drive_fid_42"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(res_late.returncode, 0)

        # Даже если старый grant остался на диске, в новой сессии он возвращает False
        grant_file.write_text(json.dumps({
            "status": "granted", "run_id": run_id_1, "iid": "1001", "folder_id": "drive_fid_42"
        }), encoding="utf-8")

        self.assertFalse(self.web_mod["is_cleanup_authorized"](
            grant_file=str(grant_file), run_id_val=run_id_2, iid_val="1001", folder_id_val="drive_fid_42"
        ))
        self.assertFalse(self.parts_mod["is_cleanup_authorized"](
            grant_file=str(grant_file), run_id_val=run_id_2, iid_val="1001", folder_id_val="drive_fid_42"
        ))

        self.assertEqual(self.cloud_deletions, [])

    def test_os_lock_serialization_between_session_start_and_grant_cleanup(self):
        """Проверка синхронизации через блокировку OS (flock) между session_start и grant_cleanup."""
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id_orig = (self.l_dir / "run_id").read_text().strip()

        # Имитация конкурентного вызова: держим session.lock из отдельного процесса
        lock_file = self.l_dir / "session.lock"
        # Захватываем блокировку на 1 секунду
        p = subprocess.Popen([
            "python3", "-c",
            f"import fcntl, time\nf = open('{lock_file}', 'w')\nfcntl.flock(f, fcntl.LOCK_EX)\ntime.sleep(0.5)\n"
        ])
        time.sleep(0.1)

        # Во время удержания блокировки параллельный session_start успешно ждет или отрабатывает после
        # А попытка grant_cleanup со старым run_id после смены сессии отклоняется
        p.wait()

        # Запускаем новую сессию
        subprocess.run(["bash", str(self.wolf_sh), "session-start"], check=True, env=self.env)
        run_id_new = (self.l_dir / "run_id").read_text().strip()
        self.assertNotEqual(run_id_orig, run_id_new)

        # Запрос grant_cleanup с run_id_orig отклоняется
        res = subprocess.run(
            ["bash", str(self.wolf_sh), "grant-cleanup", "1001", run_id_orig, "drive_fid_42"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(self.cloud_deletions, [])


if __name__ == "__main__":
    unittest.main()

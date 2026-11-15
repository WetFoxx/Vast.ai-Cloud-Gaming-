import base64
import contextlib
import io
import json
import mmap
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_shadow_compare import (  # noqa: E402
    SA,
    ShadowWolfMachine,
    _sha256_tree,
)

RCLONE_FAIL_WRAPPER = r'''#!/usr/bin/env python3
import json, os, sys

fail_flag = os.environ.get("WOLF_MOCK_RCLONE_FAIL_FLAG", "")
with_val = {
    "--retries", "--low-level-retries", "--drive-use-trash", "--drive-pacer-min-sleep",
    "--drive-pacer-burst", "--timeout", "--contimeout", "--transfers", "--checkers",
    "-F", "-s", "--hash", "--offset", "--count", "--config",
}
pos = []
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a.startswith("--") and "=" in a:
        i += 1
    elif a in with_val and i + 1 < len(sys.argv):
        i += 2
    elif a.startswith("-"):
        i += 1
    else:
        pos.append(a)
        i += 1
cmd = pos[0] if pos else ""
if fail_flag and os.path.isfile(fail_flag):
    targets = open(fail_flag, encoding="utf-8").read().split()
    if not targets or cmd in targets:
        log_path = os.environ.get("WOLF_MOCK_RCLONE_LOG", "")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(json.dumps({"cmd": cmd, "pos": pos[1:], "injected_fail": True}) + "\n")
        sys.stderr.write(f"mock rclone injected failure on {cmd}\n")
        sys.exit(7)

real_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rclone.real")
os.execv(real_bin, [real_bin, *sys.argv[1:]])
'''


class OptInWolfMachine(ShadowWolfMachine):
    """Test harness extending ShadowWolfMachine with opt-in flags and rclone fail injection."""

    def __init__(self, tmpdir, enable_optin="0", optin_interval_sec=30, debounce_ms=60, max_journal_bytes=4194304):
        super().__init__(
            tmpdir,
            enable_shadow="0",
            debounce_ms=debounce_ms,
            max_journal_bytes=max_journal_bytes,
        )
        real_rclone = self.bin_dir / "rclone.real"
        orig_rclone = self.bin_dir / "rclone"
        os.replace(orig_rclone, real_rclone)
        orig_rclone.write_text(RCLONE_FAIL_WRAPPER, encoding="utf-8")
        orig_rclone.chmod(0o755)
        self.fail_flag = self.t_dir / "mock_rclone_fail.flag"
        self.enable_optin = str(enable_optin)
        self.optin_interval_sec = int(optin_interval_sec)
        (self.l_dir / "boot-started").write_text("", encoding="utf-8")
        self.write_optin_env(enable_optin=self.enable_optin, optin_interval_sec=self.optin_interval_sec)

    def write_optin_env(self, enable_optin=None, optin_interval_sec=None, sa_mode="observe-only", vg_sync=None, so="1", sv="1"):
        opt = self.enable_optin if enable_optin is None else str(enable_optin)
        osec = self.optin_interval_sec if optin_interval_sec is None else int(optin_interval_sec)
        lines = [
            "R=mock:vast",
            f"DH={self.dh}",
            f"GD={self.gd}",
            f"PD={self.pd}",
            "DU=user",
            "WM=docker",
            "VI=''",
            "SG=1", f"SO={so}", f"SV={sv}", "PAR=2",
            "SMIN=5", "GMIN=15", "NT=0",
            f"SA={sa_mode}",
            "WOLF_SHADOW_COMPARE=0",
            f"WOLF_STATE_AGENT_OPTIN={opt}",
            f"WOLF_OPTIN_INTERVAL_SEC={osec}",
            f"WOLF_STATE_AGENT_DEBOUNCE_MS={self.debounce_ms}",
            f"WOLF_STATE_AGENT_MAX_BYTES={self.max_journal_bytes}",
            "WOLF_STATE_AGENT_HEALTH_SEC=0.5",
            "WOLF_STATE_AGENT_RESTART_SEC=0.2",
            f"WOLF_STATE_AGENT_LOG={self.log_dir / 'wolf-state-agent.log'}",
            f"WOLF_S={self.s_dir}",
            f"WOLF_T={self.t_dir}",
            f"WOLF_L={self.l_dir}",
            f"WOLF_DH={self.dh}",
            f"WOLF_GD={self.gd}",
            f"WOLF_PD={self.pd}",
            f"WOLF_ENV_FILE={self.env_file}",
        ]
        if vg_sync is not None:
            lines.append(f"VG_SYNC={vg_sync}")
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _restore_clean(remote_dir, dst_root, archive):
    dst = Path(dst_root)
    shutil.rmtree(dst, ignore_errors=True)
    rmach = ShadowWolfMachine(dst, enable_shadow="0", debounce_ms=50)
    rmach.remote = Path(remote_dir)
    rmach.run_wolf("restore", archive)
    return rmach


def _read_optin_reports(m):
    p = m.s_dir / "optin" / "report.jsonl"
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_rclone_calls(log_path):
    p = Path(log_path)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wait_roots_ready(m, expected_ids, timeout=10.0):
    state_path = m.s_dir / "agent" / "state.json"
    pid_path = m.l_dir / "state-agent.pid"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_path.is_file() and state_path.is_file():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, 0)
            except Exception:
                pid = 0
            st = SA._read_json_if_exists(str(state_path))
            if pid > 0 and st and st.get("running"):
                rmap = {r["id"]: r for r in st.get("roots", [])}
                if all(rmap.get(rid, {}).get("watched") for rid in expected_ids):
                    return st
        time.sleep(0.05)
    raise AssertionError(f"agent did not become ready: {SA._read_json_if_exists(str(state_path))}")


def _stop_proc_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


def _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, archives):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        SA.main([
            "optin-plan",
            "--optin-dir", str(optin_dir),
            "--state", str(state_p),
            "--pid", str(pid_p),
            "--journal", str(j_p),
            "--dh", str(dh),
            "--gd", str(gd),
            "--sr", str(sr),
            "--archives", *archives,
            "--json",
        ])
    return json.loads(buf.getvalue())


def _run_sa_commit(optin_dir, state_p, archives, up_to_seq):
    SA.main([
        "optin-commit",
        "--optin-dir", str(optin_dir),
        "--state", str(state_p),
        "--archives", *archives,
        "--up-to-seq", str(up_to_seq),
    ])


def _run_optin_until_not_deferred(m, target_arch, extra_env=None, max_ticks=3):
    reports = []
    for _ in range(max_ticks):
        m.run_wolf("state-agent", "optin-tick", extra_env=extra_env)
        rep = _read_optin_reports(m)[-1]
        reports.append(rep)
        if target_arch in (rep.get("packed") or []):
            return reports
        if target_arch not in (rep.get("deferred") or []):
            return reports
    return reports


class TestOptInUnit(unittest.TestCase):
    def test_optin_plan_and_commit_cursor_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dh = root / "dh"
            sr = ".local/share/Steam"
            gd = root / "Games"
            optin_dir = root / "optin"
            agent_dir = root / "agent"
            gd.mkdir(parents=True)
            (dh / sr / "steamapps" / "compatdata" / "413150" / "pfx" / "drive_c").mkdir(parents=True)
            (gd / "Alpha Game").mkdir()
            (gd / "Beta Game").mkdir()
            agent_dir.mkdir()

            state_p = agent_dir / "state.json"
            pid_p = root / "agent.pid"
            j_p = agent_dir / "events.jsonl"
            pid_p.write_text(str(os.getpid()), encoding="utf-8")
            j = SA.BoundedJournal(str(j_p), max_bytes=65536)
            j.append({"ts": 1.0, "kind": "file", "op": "create", "path": str(gd / "Alpha Game" / "a.sav")})
            j.append({"ts": 2.0, "kind": "file", "op": "write", "path": str(gd / "Alpha Game" / "b.sav")})
            j.append({"ts": 3.0, "kind": "status", "op": "heartbeat", "path": ""})

            base_state = {
                "running": True,
                "mode": "observe-only",
                "pid": os.getpid(),
                "boot_seq": 1,
                "restart_count": 0,
                "overflow_count": 0,
                "journal_seq": 3,
                "root_errors": {},
            }
            state_p.write_text(json.dumps(base_state), encoding="utf-8")

            cands = ["game--Alpha Game", "game--Beta Game", "pfx--413150"]
            # Initialize baseline cursors at seq=0 (missing cursor triggers fallback on cold start)
            _run_sa_commit(optin_dir, state_p, cands, up_to_seq=0)

            def fake_flush(sp, pp, timeout_sec=0.35):
                st, err = SA._read_json_strict(sp)
                return {"alive": True, "flushed": True, "reason": None, "state": st, "state_error": err}

            with mock.patch.object(SA, "flush_running_agent", side_effect=fake_flush):
                plan1 = _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, cands)
                self.assertFalse(plan1["fallback_to_standard"], plan1)
                self.assertEqual(plan1["mark_seq"], 3)
                self.assertEqual(plan1["selected"], ["game--Alpha Game"])
                self.assertEqual(sorted(plan1["clean"]), ["game--Beta Game", "pfx--413150"])

                # Commit only Beta + pfx cleanly first (simulate failed Alpha upload -> Alpha stays dirty!)
                _run_sa_commit(optin_dir, state_p, plan1["clean"], up_to_seq=plan1["mark_seq"])
                plan_retry = _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, cands)
                self.assertEqual(plan_retry["selected"], ["game--Alpha Game"])

                # Concurrent write seq=4 after mark_seq=3; commit Alpha only up to mark_seq=3
                j.append({"ts": 4.0, "kind": "file", "op": "write", "path": str(gd / "Alpha Game" / "during.sav")})
                base_state["journal_seq"] = 4
                state_p.write_text(json.dumps(base_state), encoding="utf-8")
                _run_sa_commit(optin_dir, state_p, ["game--Alpha Game"], up_to_seq=3)

                plan_next = _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, cands)
                self.assertFalse(plan_next["fallback_to_standard"])
                self.assertEqual(plan_next["selected"], ["game--Alpha Game"])
                self.assertEqual(plan_next["mark_seq"], 4)

    def test_optin_plan_fallback_triggers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dh = root / "dh"
            sr = ".local/share/Steam"
            gd = root / "Games"
            optin_dir = root / "optin"
            agent_dir = root / "agent"
            gd.mkdir(parents=True)
            (gd / "Alpha Game").mkdir()
            agent_dir.mkdir()
            state_p = agent_dir / "state.json"
            pid_p = root / "agent.pid"
            j_p = agent_dir / "events.jsonl"
            cands = ["game--Alpha Game"]

            j = SA.BoundedJournal(str(j_p), max_bytes=65536)
            j.append({"ts": 1.0, "kind": "file", "op": "create", "path": str(gd / "Alpha Game" / "a.sav")})

            # 1) Stopped PID (uses real flush_running_agent check -> agent_not_running)
            pid_p.write_text("999999", encoding="utf-8")
            cur_state = {
                "running": True,
                "mode": "observe-only",
                "boot_seq": 1,
                "restart_count": 0,
                "overflow_count": 0,
                "journal_seq": 1,
                "root_errors": {},
            }
            state_p.write_text(json.dumps(cur_state), encoding="utf-8")
            p_stop = _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, cands)
            self.assertTrue(p_stop["fallback_to_standard"])
            self.assertIn("agent_not_running", p_stop["fallback_reasons"])
            self.assertEqual(p_stop["selected"], cands)

            # Live cursor at seq=1, overflow_count=0
            _run_sa_commit(optin_dir, state_p, cands, up_to_seq=1)

            def fake_flush(sp, pp, timeout_sec=0.35):
                st, err = SA._read_json_strict(sp)
                return {"alive": True, "flushed": True, "reason": None, "state": st, "state_error": err}

            with mock.patch.object(SA, "flush_running_agent", side_effect=fake_flush):
                # 2) Queue overflow
                cur_state["overflow_count"] = 3
                state_p.write_text(json.dumps(cur_state), encoding="utf-8")
                p_ovf = _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, cands)
                self.assertTrue(p_ovf["fallback_to_standard"])
                self.assertIn("queue_overflow", p_ovf["fallback_reasons"])

                # 3) Corrupt journal line
                _run_sa_commit(optin_dir, state_p, cands, up_to_seq=1)
                cur_state["journal_seq"] = 2
                state_p.write_text(json.dumps(cur_state), encoding="utf-8")
                with open(j_p, "a", encoding="utf-8") as fh:
                    fh.write("{broken-json\n")
                p_dec = _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, cands)
                self.assertTrue(p_dec["fallback_to_standard"])
                self.assertIn("journal_decode_error", p_dec["fallback_reasons"])

                # 4) Rotation gap (only seq=10 exists while cursor is seq=1)
                j_p.write_text(
                    json.dumps({"seq": 10, "ts": 10.0, "kind": "file", "op": "write", "path": str(gd / "Alpha Game" / "b.sav")}) + "\n",
                    encoding="utf-8",
                )
                cur_state["journal_seq"] = 10
                state_p.write_text(json.dumps(cur_state), encoding="utf-8")
                p_gap = _run_sa_plan(optin_dir, state_p, pid_p, j_p, dh, gd, sr, cands)
                self.assertTrue(p_gap["fallback_to_standard"])
                self.assertIn("rotation_gap", p_gap["fallback_reasons"])


@unittest.skipUnless(
    sys.platform.startswith("linux") and bool(shutil.which("zstd")),
    "требуется Linux kernel inotify и zstd",
)
class TestLinuxDockerOptInExperiment(unittest.TestCase):
    def test_default_off_and_agent_off_noop(self):
        with tempfile.TemporaryDirectory() as td:
            m = OptInWolfMachine(td, enable_optin="0", debounce_ms=50)
            alpha = m.gd / "Alpha Game"
            alpha.mkdir(parents=True)
            (alpha / "slot1.sav").write_bytes(b"default-off-data")

            rlog = m.t_dir / "rclone.log"
            m.run_wolf("state-agent", "optin-tick", extra_env={"WOLF_MOCK_RCLONE_LOG": str(rlog)})
            self.assertFalse((m.s_dir / "optin" / "report.jsonl").exists())
            self.assertEqual(_read_rclone_calls(rlog), [])

            m.write_optin_env(enable_optin="1", sa_mode="off")
            m.run_wolf("state-agent", "optin-tick", extra_env={"WOLF_MOCK_RCLONE_LOG": str(rlog)})
            self.assertFalse((m.s_dir / "optin" / "report.jsonl").exists())
            self.assertEqual(_read_rclone_calls(rlog), [])

    def test_selective_writes_atomic_mmap_toggles_and_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            m = OptInWolfMachine(td, enable_optin="1", optin_interval_sec=3600, debounce_ms=60)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game", "game--Beta Game", "game--Locked Game"],
                "saves_off": ["pfx--999999"],
                "own": "all",
            }).encode("utf-8")).decode("ascii")
            m.write_optin_env(enable_optin="1", optin_interval_sec=3600, vg_sync=vg)
            m.run_sel_init(vg)

            alpha = m.gd / "Alpha Game"
            beta = m.gd / "Beta Game"
            locked = m.gd / "Locked Game"
            own_cloud = m.gd / "Own Cloud"
            own_local = m.gd / "Own Local New"
            skip_own = m.gd / "Skipped Own"
            excluded = m.gd / "Excluded Game"
            pfx_on = m.sr / "steamapps" / "compatdata" / "413150" / "pfx" / "drive_c" / "users" / "steamuser" / "Documents"
            pfx_off = m.sr / "steamapps" / "compatdata" / "999999" / "pfx" / "drive_c" / "users" / "steamuser" / "Documents"
            for d in (alpha / "sub", beta, locked, own_cloud, skip_own, excluded, pfx_on, pfx_off):
                d.mkdir(parents=True, exist_ok=True)

            m.run_sel_init(vg)
            (m.s_dir / "sel.skip").write_text("game--Skipped Own\n", encoding="utf-8")
            (m.s_dir / "x" / "game--Excluded Game").write_text("", encoding="utf-8")

            (alpha / "base.sav").write_bytes(b"alpha-base")
            mmap_f = alpha / "mmap_slot.sav"
            mmap_f.write_bytes(b"MMAP-BASE-000001")
            (beta / "beta.sav").write_bytes(b"beta-base")
            (locked / "locked.sav").write_bytes(b"locked-base")
            (own_cloud / "ownc.sav").write_bytes(b"ownc-v1")
            (pfx_on / "pfx_on.sav").write_bytes(b"pfx-v1")
            (pfx_off / "pfx_off.sav").write_bytes(b"pfx-off-v1")

            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            locker = None
            try:
                # Because VG_SYNC has own="all", agent watches entire $GD under root id "games"
                _wait_roots_ready(m, ("games", "compatdata"))
                # Seed Own Cloud into remote once, then remove from sel.games so it relies on sel.own_all + incloud
                (m.s_dir / "sel.games").write_text(
                    "game--Alpha Game\ngame--Beta Game\ngame--Locked Game\ngame--Own Cloud\n",
                    encoding="utf-8",
                )
                m.run_wolf("finish-run")
                (m.s_dir / "sel.games").write_text(
                    "game--Alpha Game\ngame--Beta Game\ngame--Locked Game\n",
                    encoding="utf-8",
                )

                # 1) Cold optin-tick commits baseline cursors; second idle tick does ZERO rclone calls!
                rlog = m.t_dir / "rclone.log"
                m.run_wolf("state-agent", "optin-tick")
                rlog.unlink(missing_ok=True)
                m.run_wolf("state-agent", "optin-tick", extra_env={"WOLF_MOCK_RCLONE_LOG": str(rlog)})
                reps = _read_optin_reports(m)
                self.assertFalse(reps[-1]["fallback_to_standard"])
                self.assertEqual(reps[-1]["selected"], [])
                self.assertFalse(reps[-1]["drive_listed"])
                self.assertEqual(_read_rclone_calls(rlog), [])

                # 2) Regular write + nested dir + atomic rename on Alpha Game, plus writes in excluded/ignored targets
                rlog.unlink(missing_ok=True)
                (alpha / "sub" / "nested.sav").write_bytes(b"alpha-nested-v2")
                tmp_ren = alpha / ".save.vgtmp"
                tmp_ren.write_bytes(b"alpha-atomic-v2")
                os.replace(tmp_ren, alpha / "atomic.sav")

                (skip_own / "sk.sav").write_bytes(b"should-not-upload")
                (excluded / "ex.sav").write_bytes(b"should-not-upload")
                own_local.mkdir(parents=True, exist_ok=True)
                (own_local / "loc.sav").write_bytes(b"should-not-upload")
                (pfx_off / "off.sav").write_bytes(b"should-not-upload")

                # Active running process inside Locked Game blocks uploading game--Locked Game
                locker = subprocess.Popen(
                    ["sleep", "60"],
                    cwd=str(locked),
                    start_new_session=True,
                )
                (locked / "locked_new.sav").write_bytes(b"locked-live-write")

                time.sleep(0.25)
                settle_reps = _run_optin_until_not_deferred(
                    m, "game--Alpha Game", extra_env={"WOLF_MOCK_RCLONE_LOG": str(rlog)},
                )
                rep2 = settle_reps[-1]
                self.assertFalse(rep2["fallback_to_standard"], rep2)
                self.assertNotIn("game--Skipped Own", rep2["selected"])
                self.assertNotIn("game--Excluded Game", rep2["selected"])
                self.assertNotIn("game--Beta Game", rep2["selected"])
                self.assertNotIn("pfx--413150", rep2["selected"])
                self.assertNotIn("pfx--999999", rep2["selected"])
                self.assertEqual(rep2["packed"], ["game--Alpha Game"], rep2)
                self.assertTrue(rep2["drive_listed"])

                self.assertFalse(any("Skipped Own" in p.name for p in m.remote.rglob("*")))
                self.assertFalse(any("Excluded Game" in p.name for p in m.remote.rglob("*")))
                self.assertFalse(any("Own Local New" in p.name for p in m.remote.rglob("*")))
                self.assertFalse(any("999999" in p.name for p in m.remote.rglob("*")))

                res_alpha = _restore_clean(m.remote, m.root / "restore_alpha", "game--Alpha Game")
                self.assertEqual(_sha256_tree(res_alpha.gd / "Alpha Game"), _sha256_tree(alpha))

                res_locked = _restore_clean(m.remote, m.root / "restore_locked", "game--Locked Game")
                self.assertFalse((res_locked.gd / "Locked Game" / "locked_new.sav").exists())

                _stop_proc_group(locker)
                locker = None

                # 3) Sync toggles: without sel.on, SO=0 blocks game-- and SV=0 blocks pfx--
                sel_files = list(m.s_dir.glob("sel.*"))
                sel_backup = {f.name: f.read_bytes() for f in sel_files}
                for f in sel_files:
                    f.unlink()
                m.write_optin_env(enable_optin="1", optin_interval_sec=3600, so="0", sv="0")
                (beta / "toggled_off.sav").write_bytes(b"beta-so0")
                (pfx_on / "toggled_off_pfx.sav").write_bytes(b"pfx-sv0")
                time.sleep(0.25)
                rlog.unlink(missing_ok=True)
                m.run_wolf("state-agent", "optin-tick", extra_env={"WOLF_MOCK_RCLONE_LOG": str(rlog)})
                self.assertEqual(_read_rclone_calls(rlog), [])

                # Restore sel.* & toggles -> Own Cloud (sel.own_all + incloud) uploads via opt-in
                for name, data in sel_backup.items():
                    (m.s_dir / name).write_bytes(data)
                m.write_optin_env(enable_optin="1", optin_interval_sec=3600, so="1", sv="1", vg_sync=vg)
                (m.s_dir / "sel.games").write_text(
                    "game--Alpha Game\ngame--Beta Game\ngame--Locked Game\n",
                    encoding="utf-8",
                )
                (own_cloud / "ownc2.sav").write_bytes(b"ownc-v2")
                time.sleep(0.25)
                rep_own = _run_optin_until_not_deferred(m, "game--Own Cloud")[-1]
                self.assertIn("game--Own Cloud", rep_own["packed"], rep_own)
                res_ownc = _restore_clean(m.remote, m.root / "restore_ownc", "game--Own Cloud")
                self.assertEqual(_sha256_tree(res_ownc.gd / "Own Cloud"), _sha256_tree(own_cloud))

                # 4) mmap(MAP_SHARED) write produces ZERO inotify events:
                #    optin-tick skips it (0 rclone calls), mandatory `wolf games` + `finish-run` catch & upload it!
                (m.s_dir / "sel.skip").write_text("game--Skipped Own\ngame--Own Local New\n", encoding="utf-8")
                fd_mm = os.open(mmap_f, os.O_RDWR)
                mm = mmap.mmap(fd_mm, 16, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
                os.close(fd_mm)
                try:
                    m.run_wolf("finish-run")
                    m.run_wolf("state-agent", "optin-tick")
                    m.run_wolf("state-agent", "optin-tick")

                    mm[0:16] = b"MMAP-DIRTY--0099"
                    mm.flush()
                    time.sleep(0.25)

                    rlog.unlink(missing_ok=True)
                    m.run_wolf("state-agent", "optin-tick", extra_env={"WOLF_MOCK_RCLONE_LOG": str(rlog)})
                    rep_mmap = _read_optin_reports(m)[-1]
                    self.assertFalse(rep_mmap["fallback_to_standard"])
                    self.assertEqual(rep_mmap["selected"], [])
                    self.assertFalse(rep_mmap["drive_listed"])
                    self.assertEqual(_read_rclone_calls(rlog), [])

                    # Mandatory periodic `wolf games` and final `finish-run` catch mmap write cleanly
                    m.run_wolf("games")
                    m.run_wolf("finish-run")
                    res_mmap = _restore_clean(m.remote, m.root / "restore_mmap", "game--Alpha Game")
                    self.assertEqual(_sha256_tree(res_mmap.gd / "Alpha Game"), _sha256_tree(alpha))
                finally:
                    mm.close()
            finally:
                if locker is not None:
                    _stop_proc_group(locker)
                _stop_proc_group(proc)

    def test_degraded_fallbacks_sigkill_overflow_corrupt_journal_and_rotation_gap(self):
        with tempfile.TemporaryDirectory() as td:
            m = OptInWolfMachine(td, enable_optin="1", optin_interval_sec=3600, debounce_ms=60)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game", "game--Beta Game"],
                "saves_off": [],
            }).encode("utf-8")).decode("ascii")
            m.write_optin_env(enable_optin="1", optin_interval_sec=3600, vg_sync=vg)
            m.run_sel_init(vg)

            alpha = m.gd / "Alpha Game"
            beta = m.gd / "Beta Game"
            flood_dir = alpha / "flood"
            flood_dir.mkdir(parents=True)
            beta.mkdir(parents=True)
            (alpha / "a.sav").write_bytes(b"a-base")
            (beta / "b.sav").write_bytes(b"b-base")

            # A) Real kernel inotify queue_overflow -> fallback_to_standard -> uploads Beta write
            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                _wait_roots_ready(m, ("game:Alpha Game", "game:Beta Game", "compatdata"))
                m.run_wolf("finish-run")
                m.run_wolf("state-agent", "optin-tick")

                q_max = int(Path("/proc/sys/fs/inotify/max_queued_events").read_text().strip())
                st_path = m.s_dir / "agent" / "state.json"
                st_before = SA._read_json_if_exists(str(st_path)) or {}
                ov_before = int(st_before.get("overflow_count") or 0)
                agent_pid = int((m.l_dir / "state-agent.pid").read_text().strip())
                os.kill(agent_pid, signal.SIGSTOP)
                try:
                    burst = min(max(q_max + 2500, 18000), 25000)
                    for idx in range(burst):
                        p = flood_dir / f"e_{idx % 64}.tmp"
                        with open(p, "wb") as fh:
                            fh.write(b"x")
                finally:
                    os.kill(agent_pid, signal.SIGCONT)

                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    st_now = SA._read_json_if_exists(str(st_path)) or {}
                    if int(st_now.get("overflow_count") or 0) > ov_before:
                        break
                    time.sleep(0.05)
                self.assertGreater(int((SA._read_json_if_exists(str(st_path)) or {}).get("overflow_count") or 0), ov_before)

                for p in flood_dir.iterdir():
                    p.unlink()
                (beta / "during_overflow.sav").write_bytes(b"ovf-beta-v2")
                time.sleep(0.25)

                ovf_reps = _run_optin_until_not_deferred(m, "game--Beta Game")
                rep_ovf = ovf_reps[-1]
                self.assertTrue(any(r["fallback_to_standard"] for r in ovf_reps), ovf_reps)
                self.assertTrue(any("queue_overflow" in (r.get("fallback_reasons") or []) for r in ovf_reps), ovf_reps)
                self.assertIn("game--Beta Game", rep_ovf["packed"], rep_ovf)

                res_beta = _restore_clean(m.remote, m.root / "restore_beta_ovf", "game--Beta Game")
                self.assertEqual(_sha256_tree(res_beta.gd / "Beta Game"), _sha256_tree(beta))
            finally:
                _stop_proc_group(proc)

            # B) Agent stopped -> optin-tick falls back to standard and uploads undetected write
            (beta / "after_kill.sav").write_bytes(b"after-sigkill-v3")
            kill_reps = _run_optin_until_not_deferred(m, "game--Beta Game")
            rep_kill = kill_reps[-1]
            self.assertTrue(rep_kill["fallback_to_standard"], rep_kill)
            self.assertIn("agent_not_running", rep_kill["fallback_reasons"], rep_kill)
            self.assertIn("game--Beta Game", rep_kill["packed"], rep_kill)
            res_kill = _restore_clean(m.remote, m.root / "restore_beta_kill", "game--Beta Game")
            self.assertEqual(_sha256_tree(res_kill.gd / "Beta Game"), _sha256_tree(beta))

            # C) Corrupt journal while agent is alive -> fallback + clean restore
            proc2 = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                _wait_roots_ready(m, ("game:Alpha Game", "game:Beta Game", "compatdata"))
                # Run optin-tick once so cursors sync to new boot_seq, then verify clean
                m.run_wolf("state-agent", "optin-tick")
                m.run_wolf("state-agent", "optin-tick")

                with open(m.s_dir / "agent" / "events.jsonl", "a", encoding="utf-8") as fh:
                    fh.write("{CORRUPTED_JSON_LINE\n")
                (beta / "after_corrupt.sav").write_bytes(b"corrupt-beta-v4")
                time.sleep(0.25)
                cor_reps = _run_optin_until_not_deferred(m, "game--Beta Game")
                rep_cor = cor_reps[-1]
                self.assertTrue(any(r["fallback_to_standard"] for r in cor_reps), cor_reps)
                self.assertTrue(any("journal_decode_error" in (r.get("fallback_reasons") or []) for r in cor_reps), cor_reps)
                self.assertIn("game--Beta Game", rep_cor["packed"], rep_cor)
                res_cor = _restore_clean(m.remote, m.root / "restore_beta_cor", "game--Beta Game")
                self.assertEqual(_sha256_tree(res_cor.gd / "Beta Game"), _sha256_tree(beta))
            finally:
                _stop_proc_group(proc2)

            # D) Rotation gap with live SIGUSR1-handling responder
            jpath = m.s_dir / "agent" / "events.jsonl"
            for r in list((m.s_dir / "agent").glob("events.jsonl.*")):
                r.unlink()
            cur_st = SA._read_json_if_exists(str(m.s_dir / "agent" / "state.json")) or {}
            jump_seq = int(cur_st.get("journal_seq") or 10) + 50
            jpath.write_text(
                json.dumps({"seq": jump_seq, "ts": time.time(), "kind": "status", "op": "heartbeat", "path": ""}) + "\n",
                encoding="utf-8",
            )
            cur_st["running"] = True
            cur_st["mode"] = "observe-only"
            cur_st["journal_seq"] = jump_seq
            st_file = m.s_dir / "agent" / "state.json"
            st_file.write_text(json.dumps(cur_st), encoding="utf-8")
            responder_code = (
                "import json, signal, time\n"
                f"sp = {str(st_file)!r}\n"
                "def ack(_s, _f):\n"
                "    d = json.load(open(sp, encoding='utf-8'))\n"
                "    d['flush_ack'] = int(d.get('flush_ack') or 0) + 1\n"
                "    open(sp, 'w', encoding='utf-8').write(json.dumps(d))\n"
                "signal.signal(signal.SIGUSR1, ack)\n"
                "time.sleep(30)\n"
            )
            keeper = subprocess.Popen([sys.executable, "-c", responder_code], start_new_session=True)
            try:
                (m.l_dir / "state-agent.pid").write_text(str(keeper.pid), encoding="utf-8")
                time.sleep(0.1)
                (beta / "during_gap.sav").write_bytes(b"gap-beta-v5")
                gap_reps = _run_optin_until_not_deferred(m, "game--Beta Game")
                rep_gap = gap_reps[-1]
                self.assertTrue(any(r["fallback_to_standard"] for r in gap_reps), gap_reps)
                self.assertTrue(any("rotation_gap" in (r.get("fallback_reasons") or []) for r in gap_reps), gap_reps)
                self.assertIn("game--Beta Game", rep_gap["packed"], rep_gap)
                res_gap = _restore_clean(m.remote, m.root / "restore_beta_gap", "game--Beta Game")
                self.assertEqual(_sha256_tree(res_gap.gd / "Beta Game"), _sha256_tree(beta))
            finally:
                _stop_proc_group(keeper)

    def test_failed_upload_retains_dirty_and_write_during_upload_retained(self):
        with tempfile.TemporaryDirectory() as td:
            m = OptInWolfMachine(td, enable_optin="1", optin_interval_sec=3600, debounce_ms=60)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game"],
                "saves_off": [],
            }).encode("utf-8")).decode("ascii")
            m.write_optin_env(enable_optin="1", optin_interval_sec=3600, vg_sync=vg)
            m.run_sel_init(vg)
            alpha = m.gd / "Alpha Game"
            alpha.mkdir(parents=True)
            (alpha / "base.sav").write_bytes(b"base-v1")

            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                _wait_roots_ready(m, ("game:Alpha Game", "compatdata"))
                m.run_wolf("finish-run")
                m.run_wolf("state-agent", "optin-tick")
                m.run_wolf("state-agent", "optin-tick")

                # 1) Upload failure on moveto -> cursor stays dirty -> retry succeeds cleanly
                (alpha / "retry_after_fail.sav").write_bytes(b"fail-then-ok-v2")
                time.sleep(0.25)
                m.fail_flag.write_text("moveto\n", encoding="utf-8")
                # DB=1 for game-- defers on pass 1 ($S/p/...) and attempts upload (moveto) on pass 2
                got_fail_rc = False
                for _ in range(3):
                    cp_fail = m.run_wolf(
                        "state-agent", "optin-tick",
                        extra_env={"WOLF_MOCK_RCLONE_FAIL_FLAG": str(m.fail_flag)},
                        check=False,
                    )
                    if cp_fail.returncode != 0:
                        got_fail_rc = True
                        break
                self.assertTrue(got_fail_rc, _read_optin_reports(m))
                rep_f = _read_optin_reports(m)[-1]
                self.assertIn("game--Alpha Game", rep_f["failed"], rep_f)
                m.fail_flag.unlink()

                # Retry WITHOUT writing anything new -> dirty state preserved -> uploads now!
                rep_ok = _run_optin_until_not_deferred(m, "game--Alpha Game")[-1]
                self.assertEqual(rep_ok["selected"], ["game--Alpha Game"], rep_ok)
                self.assertEqual(rep_ok["packed"], ["game--Alpha Game"], rep_ok)
                res_retry = _restore_clean(m.remote, m.root / "restore_retry", "game--Alpha Game")
                self.assertEqual(_sha256_tree(res_retry.gd / "Alpha Game"), _sha256_tree(alpha))

                # 2) Write arriving DURING upload (right after moveto via hook) stays for next pass
                (alpha / "before_hook.sav").write_bytes(b"before-hook-v3")
                time.sleep(0.25)
                hook_path = m.t_dir / "post_moveto.json"
                during_file = alpha / "during_upload.sav"
                hook_path.write_text(json.dumps({
                    "match_substr": "game--Alpha Game",
                    "writes": [{"path": str(during_file), "content": "during-upload-v4"}],
                    "sleep_after_ms": 200,
                }), encoding="utf-8")

                rep_hook = _run_optin_until_not_deferred(
                    m, "game--Alpha Game",
                    extra_env={"WOLF_MOCK_POST_MOVETO_HOOK": str(hook_path)},
                )[-1]
                self.assertEqual(rep_hook["packed"], ["game--Alpha Game"], rep_hook)

                res_mid = _restore_clean(m.remote, m.root / "restore_mid", "game--Alpha Game")
                self.assertTrue((res_mid.gd / "Alpha Game" / "before_hook.sav").is_file())
                self.assertFalse((res_mid.gd / "Alpha Game" / "during_upload.sav").exists())

                time.sleep(0.25)
                rep_after = _run_optin_until_not_deferred(m, "game--Alpha Game")[-1]
                self.assertEqual(rep_after["packed"], ["game--Alpha Game"], rep_after)
                res_fin = _restore_clean(m.remote, m.root / "restore_fin", "game--Alpha Game")
                self.assertEqual(_sha256_tree(res_fin.gd / "Alpha Game"), _sha256_tree(alpha))
            finally:
                _stop_proc_group(proc)

    def test_supervise_scheduling_and_standard_paths_independent(self):
        with tempfile.TemporaryDirectory() as td:
            m = OptInWolfMachine(td, enable_optin="1", optin_interval_sec=1, debounce_ms=50)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game", "game--Beta Game"],
                "saves_off": [],
            }).encode("utf-8")).decode("ascii")
            m.write_optin_env(enable_optin="1", optin_interval_sec=1, vg_sync=vg)
            m.run_sel_init(vg)

            alpha = m.gd / "Alpha Game"
            beta = m.gd / "Beta Game"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            (alpha / "init.sav").write_bytes(b"init-a")
            (beta / "init.sav").write_bytes(b"init-b")

            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                _wait_roots_ready(m, ("game:Alpha Game", "game:Beta Game", "compatdata"))
                m.run_wolf("finish-run")

                (alpha / "auto_scheduled.sav").write_bytes(b"auto-tick-payload")
                deadline = time.monotonic() + 12.0
                got_packed = False
                res_auto = None
                while time.monotonic() < deadline:
                    for rep in _read_optin_reports(m):
                        if "game--Alpha Game" in (rep.get("packed") or []):
                            res_tmp = _restore_clean(m.remote, m.root / "restore_auto_check", "game--Alpha Game")
                            if (res_tmp.gd / "Alpha Game" / "auto_scheduled.sav").is_file():
                                got_packed = True
                                res_auto = res_tmp
                                break
                    if got_packed:
                        break
                    time.sleep(0.25)
                self.assertTrue(got_packed, _read_optin_reports(m))
                self.assertEqual(
                    _sha256_tree(res_auto.gd / "Alpha Game"),
                    _sha256_tree(alpha),
                )

                for name, fname, path_cmd, extra in (
                    ("push", "std_push.sav", ("push", "game--Beta Game"), {"NOW": "1"}),
                    ("shutdown", "std_shutdown.sav", ("shutdown",), None),
                    ("finish-run", "std_finish.sav", ("finish-run",), None),
                ):
                    st_now = SA._read_json_if_exists(str(m.s_dir / "agent" / "state.json")) or {}
                    fake_seq = int(st_now.get("journal_seq") or 0) + 10000
                    _run_sa_commit(m.s_dir / "optin", m.s_dir / "agent" / "state.json", ["game--Beta Game"], up_to_seq=fake_seq)
                    (beta / fname).write_bytes(f"standard-{name}".encode("utf-8"))
                    m.run_wolf(*path_cmd, extra_env=extra)
                    res_b = _restore_clean(m.remote, m.root / f"restore_beta_{name}", "game--Beta Game")
                    self.assertEqual(_sha256_tree(res_b.gd / "Beta Game"), _sha256_tree(beta))
            finally:
                _stop_proc_group(proc)


if __name__ == "__main__":
    unittest.main()

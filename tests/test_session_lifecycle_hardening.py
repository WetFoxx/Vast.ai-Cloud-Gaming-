"""Regression & reproduction tests for session lifecycle hardening (emergency delete, cancel_start, finish safety, steam_off)."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
import gui  # noqa: E402
import history  # noqa: E402
import jobs  # noqa: E402
import market  # noqa: E402
import rental_journal  # noqa: E402
import settings  # noqa: E402
from vastapi import VastgameError  # noqa: E402

WOLF_SH = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")


def _extract_func(name):
    m = (re.search(rf"^{name}\(\) *\{{[^\n]*\}}$", WOLF_SH, re.M)
         or re.search(rf"^{name}\(\) *\{{.*?\n\}}$", WOLF_SH, re.S | re.M))
    assert m, f"function {name} not found in wolf-setup.sh"
    return m.group(0)


class _DummyUI:
    def __init__(self, cancelled=False):
        self._cancelled = cancelled
        self.events = []
        self.stages = []
        self.archives = []
        self.meta = {}

    def cancelled(self):
        return self._cancelled

    def set_meta(self, **kw):
        self.meta.update(kw)

    def say(self, text, level="info", key=None, **params):
        self.events.append((level, key, params, text))

    def stage(self, key, text):
        self.stages.append((key, text))

    def archive(self, name, state, text):
        self.archives.append((name, state, text))

    def tailscale(self, url):
        pass


class TestEmergencyDeleteAndCancel(unittest.TestCase):
    """Emergency delete must be independent of busy state, Steam/Moonlight/Drive/Tailscale, and verify Vast API."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self._patch(settings, "FILE", self.td / "settings.json")
        self._patch(history, "FILE", self.td / "history.json")
        self._patch(market, "BAD_HOSTS", self.td / "bad_hosts.json")
        self._patch(market, "STARTS", self.td / "starts.json")
        self._patch(jobs, "STATE", self.td / "job.json")
        self._patch(jobs, "LOCK", self.td / "job.lock")
        self._patch(jobs, "CANCEL", self.td / "job.cancel")
        self._patch(jobs, "LOG", self.td / "job.log")
        self._patch(rental_journal, "FILE", self.td / "rental_journal.json")
        self._patch(rental_journal, "RECONCILE_STEP", 0.0)
        self._patch(jobs.notify, "job_finished", lambda *a, **k: None)
        self._patch(core.time, "sleep", lambda s: None)
        self._patch(jobs.time, "sleep", lambda s: None)
        self.threads = []

        def launch_in_thread(kind, args):
            t = threading.Thread(target=jobs.work, args=(kind, args), daemon=True)
            t.start()
            self.threads.append(t)

        self._patch(jobs, "_launch", launch_in_thread)
        self.api = gui.Api()

    def _patch(self, obj, name, val):
        p = mock.patch.object(obj, name, val)
        p.start()
        self.addCleanup(p.stop)

    def _join_threads(self):
        for t in self.threads:
            t.join(timeout=3)

    def test_emergency_delete_works_while_play_is_stuck_connecting_and_prevents_replacement(self):
        """Case B: app stuck connecting/loading during play -> emergency delete still works & stops replacement."""
        rented = []
        deleted = []
        entered_wait = threading.Event()
        alive = {42}

        def fake_rent(oid, body, offer=None, tpl=None):
            rented.append(oid)
            return 42, time.time()

        def fake_wait_ready(iid, t0, o, ui, docker=False):
            entered_wait.set()
            for _ in range(200):
                if ui.cancelled():
                    raise VastgameError("cancelled", key="core.start_cancelled", id=iid)
                time.sleep(0.01)
            raise VastgameError("try another", key="core.try_another")

        def fake_list():
            return [{"id": iid, "actual_status": "running"} for iid in sorted(alive)]

        def fake_delete(iid):
            deleted.append(iid)
            alive.discard(iid)
            return True, ""

        self._patch(core, "template", lambda *a, **k: {"id": 1, "disk_space": 100})
        self._patch(core, "require_no_machine", lambda: None)
        self._patch(core, "offer_by_id", lambda oid, disk, docker=False: {"id": oid, "machine_id": oid, "dph_total": 0.2})
        self._patch(core, "rental_body", lambda *a, **k: {})
        self._patch(core, "rent", fake_rent)
        self._patch(core, "wait_ready", fake_wait_ready)
        self._patch(core, "list_instances", fake_list)
        self._patch(core, "delete_instance", fake_delete)
        self._patch(core, "_still_there", lambda iid: iid in alive)

        # Start play with spare offers to ensure no replacement is rented after emergency delete
        self.assertEqual(self.api.play(10, spare=[11, 12]), {"ok": True})
        self.assertTrue(entered_wait.wait(timeout=3), "play did not reach wait_ready")
        self.assertTrue(self.api._busy(), "app should be busy while play is connecting")

        # Emergency delete MUST succeed even while busy!
        r = self.api.emergency_delete(42)
        self.assertEqual(r, {"ok": True, "gone": True})
        self._join_threads()

        self.assertEqual(deleted, [42])
        self.assertEqual(rented, [10], "play must NOT rent a replacement after emergency delete")
        # Job must be marked done and NOT auto-resumed by jobs.state()
        st = jobs.state()
        self.assertTrue(st["done"])
        self.assertFalse(st["active"])

    def test_emergency_delete_works_while_stop_is_hung_and_independent_of_moonlight_and_wolf(self):
        """Emergency delete succeeds while stop is hung, even if Moonlight/wolf/Tailscale fail."""
        alive = {55}
        deleted = []
        stop_entered = threading.Event()

        def hung_stop(ui, dry_run=False, expected_id=None):
            ui.set_meta(iid=55)
            stop_entered.set()
            for _ in range(200):
                if ui.cancelled():
                    raise VastgameError("aborted", key="core.emergency_aborted", id=55)
                time.sleep(0.01)
            return {"iid": 55, "uploaded": [], "deleted": False}

        def fake_list():
            return [{"id": iid, "actual_status": "running"} for iid in sorted(alive)]

        def fake_delete(iid):
            deleted.append(iid)
            alive.discard(iid)
            return True, ""

        self._patch(core, "stop", hung_stop)
        self._patch(core, "list_instances", fake_list)
        self._patch(core, "delete_instance", fake_delete)
        self._patch(core, "wolf_status", lambda ip: None)
        self._patch(core, "ts_node", lambda: None)
        # Even if moonlight.streaming() raises an error, emergency_delete must not fail
        self._patch(gui.moonlight, "streaming", mock.Mock(side_effect=RuntimeError("moonlight broken")))

        self.assertEqual(self.api.stop(), {"ok": True})
        self.assertTrue(stop_entered.wait(timeout=3))
        self.assertTrue(self.api._busy())

        r = self.api.emergency_delete(55)
        self.assertEqual(r, {"ok": True, "gone": True})
        self._join_threads()
        self.assertEqual(deleted, [55])

    def test_emergency_delete_when_api_delete_response_lost_then_verified_gone(self):
        """DELETE raises network timeout, but _still_there confirms instance disappeared -> confirmed gone."""
        self._patch(core, "list_instances", lambda: [{"id": 77}])
        self._patch(core, "delete_instance", mock.Mock(side_effect=VastgameError("timeout", key="vast.net")))
        self._patch(core, "_still_there", lambda iid: False)

        r = self.api.emergency_delete(77)
        self.assertEqual(r, {"ok": True, "gone": True})

    def test_emergency_delete_when_api_delete_response_lost_and_uncertain(self):
        """DELETE raises timeout and _still_there is None -> returns core.delete_uncertain, NEVER false success."""
        self._patch(core, "list_instances", lambda: [{"id": 77}])
        self._patch(core, "delete_instance", mock.Mock(side_effect=VastgameError("timeout", key="vast.net")))
        self._patch(core, "_still_there", lambda iid: None)

        r = self.api.emergency_delete(77)
        self.assertIn("error", r)
        self.assertEqual(r["error"]["key"], "core.delete_uncertain")
        self.assertEqual(r["error"]["params"]["id"], 77)

    def test_emergency_delete_when_api_returns_error_and_instance_still_alive(self):
        """DELETE fails and instance is still running -> returns core.delete_failed."""
        self._patch(core, "list_instances", lambda: [{"id": 77}])
        self._patch(core, "delete_instance", lambda iid: (False, "500 internal error"))
        self._patch(core, "_still_there", lambda iid: True)

        r = self.api.emergency_delete(77)
        self.assertIn("error", r)
        self.assertEqual(r["error"]["key"], "core.delete_failed")

    def test_emergency_delete_when_verification_list_fails_after_delete_ok(self):
        """DELETE returns ok=True, but all verification list_instances calls fail -> core.delete_uncertain."""
        calls = {"n": 0}

        def flaky_list():
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"id": 77}]
            raise VastgameError("network down", key="vast.net")

        self._patch(core, "list_instances", flaky_list)
        self._patch(core, "delete_instance", lambda iid: (True, ""))

        r = self.api.emergency_delete(77)
        self.assertIn("error", r)
        self.assertEqual(r["error"]["key"], "core.delete_uncertain")

    def test_emergency_delete_retry_after_pending_succeeds(self):
        """First emergency_delete sees instance still in list (gone=False); retry confirms gone=True."""
        alive = {88}
        calls = {"n": 0}

        def list_fn():
            calls["n"] += 1
            # Still present during first attempt's 6 polls, gone on second attempt's poll
            if calls["n"] <= 8:
                return [{"id": 88}]
            return []

        self._patch(core, "list_instances", list_fn)
        self._patch(core, "delete_instance", lambda iid: (True, ""))

        r1 = self.api.emergency_delete(88)
        self.assertEqual(r1, {"ok": True, "gone": False})

        r2 = self.api.emergency_delete(88)
        self.assertEqual(r2, {"ok": True, "gone": True})

    def test_emergency_delete_proceeds_even_if_initial_list_instances_fails(self):
        """If initial list_instances fails, DELETE is still sent for user-confirmed expected_id."""
        calls = {"n": 0}
        deleted = []

        def list_fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise VastgameError("temporary blip", key="vast.net")
            return []

        def delete_fn(iid):
            deleted.append(iid)
            return True, ""

        self._patch(core, "list_instances", list_fn)
        self._patch(core, "delete_instance", delete_fn)

        r = self.api.emergency_delete(91)
        self.assertEqual(r, {"ok": True, "gone": True})
        self.assertEqual(deleted, [91])

    def test_cancel_start_discovers_unrecorded_instance_by_run_id_and_ignores_unrelated(self):
        """If meta.iid wasn't written yet, cancel_start recovers ONLY vastgame:<run_id> and ignores foreign instances."""
        j = jobs.FileJob("play", pid=None, args={"offer_id": 5})
        alive = {64, 999}
        deleted = []

        self._patch(core, "list_instances", lambda: [
            {"id": 64, "label": f"vastgame:{j.run_id}"},
            {"id": 999, "label": "my-ml-training"},
        ] if 64 in alive else [{"id": 999, "label": "my-ml-training"}])
        self._patch(core, "delete_instance", lambda iid: (deleted.append(iid) or True, "") if not alive.discard(iid) else (True, ""))
        self._patch(core, "cancel_delete", lambda iid: (deleted.append(iid) or True, None) if not alive.discard(iid) else (True, None))
        self._patch(core, "_still_there", lambda iid: iid in alive)

        r = self.api.cancel_start()
        self.assertEqual(r, {"ok": True, "iid": 64, "iids": [64]})
        self.assertEqual(deleted, [64])
        self.assertIn(999, alive, "Unrelated user instance must NEVER be deleted")

    def test_cancel_start_refuses_to_delete_unattributed_foreign_instances(self):
        """If meta.iid is missing and no instance matches vastgame:<run_id>, refuse automatic deletion."""
        jobs.FileJob("play", pid=None, args={"offer_id": 5})
        deleted = []
        self._patch(core, "list_instances", lambda: [{"id": 888, "label": "other-work"}])
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))
        self._patch(core, "cancel_delete", lambda iid: deleted.append(iid) or (True, None))

        r = self.api.cancel_start()
        self.assertIn("error", r)
        self.assertIn(r["error"]["key"], ("core.instance_unconfirmed", "core.delete_uncertain"))
        self.assertEqual(deleted, [])

    def test_emergency_delete_without_id_refuses_unattributed_instance(self):
        """emergency_delete(None) must NEVER guess an instance just because it is the only one in Vast."""
        deleted = []
        self._patch(core, "list_instances", lambda: [{"id": 501, "label": "vastgame"}])
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))

        r = self.api.emergency_delete(None)
        self.assertIn("error", r)
        self.assertEqual(r["error"]["key"], "core.instance_unconfirmed")
        self.assertEqual(deleted, [])

    def test_cancel_during_in_flight_rent_deletes_immediately(self):
        """TOCTOU: CANCEL arrives while PUT asks/ is in-flight -> _race immediately deletes the new instance."""
        deleted = []

        def slow_rent(oid, body, offer=None, tpl=None):
            # Simulate user clicking emergency delete / cancel while rent() HTTP call is in-flight
            jobs.CANCEL.write_text("emergency:test")
            return 303, time.time()

        self._patch(core, "offer_by_id", lambda oid, disk, docker=False: {"id": oid, "machine_id": oid, "dph_total": 0.2})
        self._patch(core, "rental_body", lambda *a, **k: {"label": k.get("label") or "vastgame"})
        self._patch(core, "rent", slow_rent)
        self._patch(core, "_delete_quietly", lambda iid: deleted.append(iid) or (True, ""))
        self._patch(core, "_still_there", lambda iid: False)

        j = jobs.FileJob("play", pid=os.getpid(), args={"offer_id": 10})
        with self.assertRaises(VastgameError) as ctx:
            jobs._race(j, [10, 11], {"id": 1}, 100, None)
        self.assertEqual(ctx.exception.key, "core.start_cancelled")
        self.assertEqual(deleted, [303])

    def test_sigterm_ignoring_worker_cannot_hang_abort_or_clobber_job_json(self):
        """Worker ignores SIGTERM -> abort_active finishes promptly and stale FileJob cannot overwrite job.json."""
        stubborn = subprocess.Popen(
            ["bash", "-c", "trap '' TERM; while true; do sleep 0.1; done"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: (stubborn.kill(), stubborn.wait()))
        j = jobs.FileJob("play", pid=stubborn.pid, args={"offer_id": 5})
        j.set_meta(iid=42)

        t0 = time.monotonic()
        res = jobs.abort_active(iid=42)
        elapsed = time.monotonic() - t0
        self.assertTrue(res["aborted"])
        self.assertLess(elapsed, 2.0, "abort_active must not hang if worker ignores SIGTERM")

        # Stale worker tries to update state and finish() after abort -> MUST be ignored!
        j.say("late event")
        j.finish(result={"links": {}, "minutes": 1, "boot": [], "notes": []})

        # Simulate app restart reading job.json fresh
        disk = jobs.load()
        self.assertTrue(disk["done"])
        self.assertEqual(disk["error"]["key"], "core.emergency_aborted")
        self.assertIsNone(disk["result"])
        # App restart calling jobs.state() must NOT resume aborted job
        st = jobs.state()
        self.assertTrue(st["done"])
        self.assertFalse(st["active"])

    def test_concurrent_emergency_delete_blocks_duplicate_request(self):
        """Double-clicking emergency_delete must not send conflicting duplicate DELETE requests."""
        gate = threading.Event()
        entered = threading.Event()
        calls = []

        def slow_delete(iid):
            calls.append(iid)
            entered.set()
            gate.wait(timeout=3)
            return True

        self._patch(core, "emergency_delete", slow_delete)
        out = {}

        def first():
            out["first"] = self.api.emergency_delete(42)

        t = threading.Thread(target=first, daemon=True)
        t.start()
        self.assertTrue(entered.wait(timeout=3))
        second = self.api.emergency_delete(42)
        gate.set()
        t.join(timeout=3)

        self.assertEqual(second["error"]["key"], "busy")
        self.assertEqual(out["first"], {"ok": True, "gone": True})
        self.assertEqual(calls, [42])

    def test_cancel_start_does_not_claim_success_when_list_or_verify_fails(self):
        """cancel_start must return error if Vast API fails when discovering or verifying deletion."""
        jobs.FileJob("play", pid=None, args={"offer_id": 5})
        self._patch(core, "list_instances", mock.Mock(side_effect=VastgameError("api down", key="vast.net")))

        r = self.api.cancel_start()
        self.assertIn("error", r)
        self.assertIn(r["error"]["key"], ("vast.net", "core.delete_uncertain"))

        # Now with known iid in meta, but instance remains alive after DELETE
        j = jobs.FileJob("play", pid=None, args={"offer_id": 5})
        j.set_meta(iid=65)
        self._patch(core, "cancel_delete", lambda iid: (True, None))
        self._patch(core, "_still_there", lambda iid: True)
        r2 = self.api.cancel_start()
        self.assertIn("error", r2)
        self.assertEqual(r2["error"]["key"], "core.delete_pending")

    def test_aborted_job_is_not_resumed_by_state(self):
        """After abort_active sets CANCEL, jobs.resume() must refuse to restart the job."""
        j = jobs.FileJob("play", pid=None, args={"offer_id": 5})
        j.set_meta(iid=42)
        res = jobs.abort_active(iid=42)
        self.assertTrue(res["aborted"])
        self.assertFalse(jobs.resume(jobs.load()))


class TestNormalStopStillProtectsSaves(unittest.TestCase):
    """Normal stop with upload must never delete on archive/Drive error or unreachable machine."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self._patch(core.time, "sleep", lambda s: None)

    def _patch(self, obj, name, val):
        p = mock.patch.object(obj, name, val)
        p.start()
        self.addCleanup(p.stop)

    def test_finish_failed_protects_instance_and_includes_id(self):
        deleted = []
        self._patch(core, "_find_machine", lambda: (42, "100.64.0.1", "vg"))
        self._patch(core, "wolf_status", mock.Mock(side_effect=[
            {"boot": (100, "ok", "ok")},
            {"boot": (100, "ok", "ok"), "finish": (205, "err", "выгрузка не подтверждена: shutdown"), "shutdown": (204, "err", "Steam не закрылся вовремя")},
        ]))
        self._patch(core, "_on_machine", mock.Mock(side_effect=[
            (0, "ok"),
            (0, "200"),
            (0, "finish: запущено"),
        ]))
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))

        ui = _DummyUI()
        with self.assertRaises(VastgameError) as ctx:
            core.stop(ui)
        self.assertEqual(ctx.exception.key, "core.finish_failed")
        self.assertEqual(ctx.exception.params["id"], 42)
        self.assertEqual(deleted, [])
        self.assertEqual(ui.meta.get("iid"), 42)

    def test_connection_loss_after_archive_err_does_not_claim_offline_success(self):
        self._patch(core, "wolf_status", mock.Mock(side_effect=[
            {"finish": (201, "up", "running"), "pfx--100": (202, "err", "rclone error")},
            None,
        ]))
        self._patch(core, "_alive", lambda iid: False)

        ui = _DummyUI()
        with self.assertRaises(VastgameError) as ctx:
            core._follow_finish(ui, 42, "100.64.0.1", 200)
        self.assertEqual(ctx.exception.key, "core.deleted_during")

    def test_unreachable_status_page_refuses_delete_and_includes_id(self):
        deleted = []
        self._patch(core, "_find_machine", lambda: (42, "100.64.0.1", "vg"))
        self._patch(core, "STATUS_WAIT", 0)
        self._patch(core, "wolf_status", lambda ip: None)
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))

        with self.assertRaises(VastgameError) as ctx:
            core.stop(_DummyUI())
        self.assertEqual(ctx.exception.key, "core.no_status_page")
        self.assertEqual(ctx.exception.params["id"], 42)
        self.assertEqual(deleted, [])

    def test_stop_double_click_is_idempotent(self):
        with mock.patch.object(gui.jobs, "state", lambda: {"kind": "stop", "active": True, "done": False}):
            r = gui.Api().stop()
        self.assertEqual(r, {"ok": True, "attached": True})


class TestSteamOffInWolfSetup(unittest.TestCase):
    """Reproducible bash tests for steam_off() in wolf-setup.sh (fast-exit race & updater ignoring -shutdown)."""

    def _build_harness(self, td, fake_steam_behavior, shutdown_cmd_behavior):
        """Build an isolated bash harness running the real steam_off() and helpers from wolf-setup.sh."""
        bin_dir = td / "bin"
        bin_dir.mkdir()

        # Fake setsid (execs directly in new session via python3)
        setsid_py = bin_dir / "setsid"
        setsid_py.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "try:\n    os.setsid()\nexcept OSError:\n    pass\n"
            "os.execvp(sys.argv[1], sys.argv[1:])\n",
            encoding="utf-8",
        )
        setsid_py.chmod(0o755)

        # Fake runuser (strips -u user -- and execs the command directly)
        runuser_sh = bin_dir / "runuser"
        runuser_sh.write_text(
            "#!/usr/bin/env bash\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = \"--\" ]; then shift; break; fi\n"
            "  shift\n"
            "done\n"
            "exec \"$@\"\n",
            encoding="utf-8",
        )
        runuser_sh.chmod(0o755)

        # Fake pgrep: reads fake_steam.pid for `-x steam`, fake_reaper.pid for `-x reaper`, delegates rest
        real_pgrep = shutil.which("pgrep") or "/usr/bin/pgrep"
        pgrep_sh = bin_dir / "pgrep"
        pgrep_sh.write_text(
            f"#!/usr/bin/env bash\n"
            f"if [ \"$1\" = \"-x\" ] && [ \"$2\" = \"steam\" ]; then\n"
            f"  if [ -s \"{td}/fake_steam.pid\" ]; then\n"
            f"    p=$(cat \"{td}/fake_steam.pid\")\n"
            f"    if kill -0 \"$p\" 2>/dev/null; then echo \"$p\"; exit 0; fi\n"
            f"  fi\n"
            f"  exit 1\n"
            f"fi\n"
            f"if [ \"$1\" = \"-x\" ] && [ \"$2\" = \"reaper\" ]; then\n"
            f"  if [ -s \"{td}/fake_reaper.pid\" ]; then\n"
            f"    p=$(cat \"{td}/fake_reaper.pid\")\n"
            f"    if kill -0 \"$p\" 2>/dev/null; then echo \"$p\"; exit 0; fi\n"
            f"  fi\n"
            f"  exit 1\n"
            f"fi\n"
            f"if [ \"$1\" = \"-x\" ] && [ \"$2\" = \"steamwebhelper\" ]; then\n"
            f"  exit 1\n"
            f"fi\n"
            f"exec \"{real_pgrep}\" \"$@\"\n",
            encoding="utf-8",
        )
        pgrep_sh.chmod(0o755)

        # Fake steam binary invoked by `steam_off` as `$WOLF_STEAM_BIN -shutdown`
        steam_bin = td / "fake-steam-cmd"
        steam_bin.write_text("#!/usr/bin/env bash\n" + shutdown_cmd_behavior + "\n", encoding="utf-8")
        steam_bin.chmod(0o755)

        # Fake running steam daemon
        daemon_sh = td / "fake-steam-daemon"
        daemon_sh.write_text("#!/usr/bin/env bash\n" + fake_steam_behavior + "\n", encoding="utf-8")
        daemon_sh.chmod(0o755)

        funcs = "\n\n".join(
            _extract_func(fn)
            for fn in ("_pg_alive", "_pg_kill", "_pid_alive", "_stop_cmd_group", "_cmd_kill",
                       "_steam_running", "_steam_term", "_steam_settle", "steam_off")
        )
        runner = td / "run.sh"
        runner.write_text(
            f"#!/usr/bin/env bash\n"
            f"export PATH=\"{bin_dir}:$PATH\"\n"
            f"DU=\"$USER\" DH=\"{td}\" D=\":0\" UI=\"$(id -u)\"\n"
            f"export WOLF_STEAM_BIN=\"{steam_bin}\"\n"
            f"export WOLF_STEAM_OFF_TIMEOUT=\"${{WOLF_STEAM_OFF_TIMEOUT:-2}}\"\n"
            f"export WOLF_STEAM_OFF_TERM_GRACE=\"${{WOLF_STEAM_OFF_TERM_GRACE:-2}}\"\n"
            f"log() {{ echo \"LOG: $*\"; }}\n"
            f"{funcs}\n"
            f"setsid \"{daemon_sh}\" & \n"
            f"DAEMON_PID=$!\n"
            f"echo \"$DAEMON_PID\" > \"{td}/fake_steam.pid\"\n"
            f"sleep 0.1\n"
            f"steam_off\n"
            f"rc=$?\n"
            f"kill -9 \"$DAEMON_PID\" 2>/dev/null || true\n"
            f"wait \"$DAEMON_PID\" 2>/dev/null || true\n"
            f"exit $rc\n",
            encoding="utf-8",
        )
        runner.chmod(0o755)
        return runner

    def test_steam_off_fast_exit_does_not_fail_isolation_race(self):
        """Repro 1: steam -shutdown closes Steam and exits immediately -> steam_off must return 0."""
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        runner = self._build_harness(
            td,
            fake_steam_behavior="trap 'exit 0' USR1 TERM; while true; do sleep 0.05; done",
            shutdown_cmd_behavior=f"p=$(cat '{td}/fake_steam.pid'); kill -USR1 \"$p\" 2>/dev/null || true; exit 0",
        )
        proc = subprocess.run([str(runner)], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, f"steam_off failed on fast clean exit:\n{proc.stdout}\n{proc.stderr}")

    def test_steam_off_escalates_to_sigterm_when_steam_ignores_shutdown(self):
        """Repro 2: Steam updater ignores -shutdown, but exits cleanly on SIGTERM -> steam_off must return 0."""
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        runner = self._build_harness(
            td,
            fake_steam_behavior="trap 'exit 0' TERM; while true; do sleep 0.05; done",
            shutdown_cmd_behavior="exit 0",  # -shutdown ignored by updater
        )
        proc = subprocess.run([str(runner)], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, f"steam_off did not escalate to SIGTERM:\n{proc.stdout}\n{proc.stderr}")

    def test_steam_off_still_fails_safely_when_steam_ignores_sigterm(self):
        """Save protection: if Steam ignores both -shutdown and SIGTERM, steam_off returns 1 (no blind SIGKILL)."""
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        runner = self._build_harness(
            td,
            fake_steam_behavior="trap '' TERM; while true; do sleep 0.05; done",
            shutdown_cmd_behavior="exit 0",
        )
        env = dict(os.environ, WOLF_STEAM_OFF_TIMEOUT="1", WOLF_STEAM_OFF_TERM_GRACE="1")
        proc = subprocess.run([str(runner)], capture_output=True, text=True, env=env, timeout=15)
        self.assertNotEqual(proc.returncode, 0, "steam_off must return non-zero if Steam refuses SIGTERM")

    def test_steam_off_fails_safely_if_game_reaper_still_running(self):
        """Save protection: if Steam exits but a game (reaper) is still running, steam_off returns 1."""
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        reaper = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (reaper.kill(), reaper.wait()))
        (td / "fake_reaper.pid").write_text(str(reaper.pid), encoding="utf-8")
        runner = self._build_harness(
            td,
            fake_steam_behavior="trap 'exit 0' USR1 TERM; while true; do sleep 0.05; done",
            shutdown_cmd_behavior=f"p=$(cat '{td}/fake_steam.pid'); kill -USR1 \"$p\" 2>/dev/null || true; exit 0",
        )
        env = dict(os.environ, WOLF_STEAM_OFF_SETTLE="1")
        proc = subprocess.run([str(runner)], capture_output=True, text=True, env=env, timeout=15)
        self.assertNotEqual(proc.returncode, 0, "steam_off must fail safely while game reaper is still alive")

    def test_steam_off_handles_steam_exiting_just_before_sigterm(self):
        """Scenario 3.5: Steam exits on its own right as shutdown timeout expires -> returns 0 cleanly."""
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        runner = self._build_harness(
            td,
            fake_steam_behavior="sleep 0.9; exit 0",
            shutdown_cmd_behavior="exit 0",
        )
        env = dict(os.environ, WOLF_STEAM_OFF_TIMEOUT="1", WOLF_STEAM_OFF_TERM_GRACE="1", WOLF_STEAM_OFF_SETTLE="0")
        proc = subprocess.run([str(runner)], capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(proc.returncode, 0, f"steam_off failed when Steam exited near timeout:\n{proc.stdout}\n{proc.stderr}")


class TestUiEmergencyContracts(unittest.TestCase):
    """Static checks ensuring UI exposes emergency delete even when busy and warns about billing."""

    def test_ui_never_hides_emergency_on_busy_and_has_progress_buttons(self):
        app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
        i18n_js = (ROOT / "ui/i18n.js").read_text(encoding="utf-8")

        self.assertNotIn('$("emergency").hidden = !m || !!data.busy', app_js)
        self.assertIn('$("emergency").hidden = !m;', app_js)
        self.assertIn('id="progress-emergency"', index_html)
        self.assertIn('id="finish-emergency"', index_html)
        self.assertIn("https://cloud.vast.ai/instances/", app_js)
        for key in ("core.delete_uncertain", "core.emergency_aborted", "core.instance_unconfirmed", "emergency.open_vast"):
            self.assertEqual(i18n_js.count(f'"{key}"'), 3, f"{key} must exist in all 3 languages")


if __name__ == "__main__":
    unittest.main()

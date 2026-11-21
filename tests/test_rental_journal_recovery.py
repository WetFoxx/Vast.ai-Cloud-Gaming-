"""Stage F deterministic tests: persistent rental journal, lost rent responses, SIGTERM mid-rent, crash recovery, reconciliation."""

import json
import os
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


class TestRentalJournalAndCrashRecovery(unittest.TestCase):
    """All 18 mandatory Stage F scenarios with deterministic synchronization."""

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

    # 1. Rental succeeds and response received
    def test_01_normal_rental_transitions_to_completed(self):
        self._patch(core, "template", lambda *a, **k: {"id": 1, "disk_space": 100})
        self._patch(core, "require_no_machine", lambda: None)
        self._patch(core, "offer_by_id", lambda oid, disk, docker=False: {"id": oid, "machine_id": oid, "dph_total": 0.2})
        self._patch(core, "rental_body", lambda *a, **k: {"label": k.get("label") or "vastgame"})
        self._patch(core, "rent", lambda oid, body, offer=None, tpl=None: (101, time.time()))
        self._patch(core, "list_instances", lambda: [{"id": 101, "actual_status": "running"}])
        self._patch(core, "wait_ready", lambda iid, t0, o, ui, docker=False: {"links": {}, "minutes": 1, "boot": ("ok",)})
        self._patch(core, "identity_notes", lambda r: [])

        self.assertEqual(self.api.play(10), {"ok": True})
        self._join_threads()

        d = jobs.load()
        entry = rental_journal.get(d["run_id"])
        self.assertEqual(entry["state"], rental_journal.COMPLETED)
        self.assertEqual(entry["iid"], 101)
        self.assertEqual(rental_journal.unresolved(), [])

    # 2. Vast.ai created instance, but HTTP response lost
    def test_02_lost_rent_response_persists_uncertain_journal_entry(self):
        self._patch(core, "template", lambda *a, **k: {"id": 1, "disk_space": 100})
        self._patch(core, "require_no_machine", lambda: None)
        self._patch(core, "offer_by_id", lambda oid, disk, docker=False: {"id": oid, "machine_id": oid, "dph_total": 0.2})
        self._patch(core, "rental_body", lambda *a, **k: {"label": k.get("label") or "vastgame"})
        self._patch(core, "rent", mock.Mock(side_effect=VastgameError("connection reset", key="vast.net")))

        self.assertEqual(self.api.play(10), {"ok": True})
        self._join_threads()

        d = jobs.load()
        entry = rental_journal.get(d["run_id"])
        self.assertEqual(entry["state"], rental_journal.UNCERTAIN)
        self.assertEqual(entry["reason"], "rent_response_lost")
        self.assertEqual(len(rental_journal.unresolved()), 1)

    # 3. Cancel arrives while PUT asks/ is in-flight (deterministic threading.Event barrier)
    def test_03_cancel_during_in_flight_put_asks_deletes_instance_and_marks_deleted(self):
        in_rent = threading.Event()
        release_rent = threading.Event()
        deleted = []
        alive = set()

        def blocking_rent(oid, body, offer=None, tpl=None):
            alive.add(303)
            in_rent.set()
            release_rent.wait(timeout=3)
            return 303, time.time()

        self._patch(core, "offer_by_id", lambda oid, disk, docker=False: {"id": oid, "machine_id": oid, "dph_total": 0.2})
        self._patch(core, "rental_body", lambda *a, **k: {"label": k.get("label") or "vastgame"})
        self._patch(core, "rent", blocking_rent)
        self._patch(core, "_delete_quietly", lambda iid: (deleted.append(iid) or True, "") if not alive.discard(iid) else (True, ""))
        self._patch(core, "_still_there", lambda iid: iid in alive)

        j = jobs.FileJob("play", pid=os.getpid(), args={"offer_id": 10})
        rental_journal.mark_prepared(j.run_id, offer_id=10)
        err_box = {}

        def run_race():
            try:
                jobs._race(j, [10], {"id": 1}, 100, None)
            except VastgameError as e:
                err_box["err"] = e

        t = threading.Thread(target=run_race, daemon=True)
        t.start()
        self.assertTrue(in_rent.wait(timeout=3))
        # Journal MUST already be in CREATING before PUT asks/ returns
        self.assertEqual(rental_journal.get(j.run_id)["state"], rental_journal.CREATING)

        # User cancels while PUT asks/ is in-flight
        rental_journal.mark_cancel_requested(j.run_id)
        jobs.CANCEL.write_text(f"emergency:{j.run_id}")
        release_rent.set()
        t.join(timeout=3)

        self.assertEqual(err_box["err"].key, "core.start_cancelled")
        self.assertEqual(deleted, [303])
        self.assertEqual(rental_journal.get(j.run_id)["state"], rental_journal.DELETED)

    # 4. CRITICAL SCENARIO: Worker killed by SIGTERM before core.rent() returns iid
    def test_04_worker_killed_by_sigterm_mid_rent_is_reconciled_and_deleted(self):
        # Spawn a real child worker that exits on SIGTERM (simulating process killed mid-HTTP)
        worker = subprocess.Popen(["bash", "-c", "trap 'exit 0' TERM; while true; do sleep 0.05; done"])
        self.addCleanup(lambda: (worker.kill(), worker.wait()) if worker.poll() is None else None)

        j = jobs.FileJob("play", pid=worker.pid, args={"offer_id": 10})
        run_id = j.run_id
        # Worker persisted CREATING right before PUT asks/, then Vast.ai created 701, but worker hasn't saved meta.iid yet!
        rental_journal.mark_creating(run_id, offer_id=10)
        alive = {701: f"vastgame:{run_id}", 999: "unrelated-ml-box"}
        deleted = []

        def fake_list():
            return [{"id": iid, "label": lbl} for iid, lbl in sorted(alive.items())]

        def fake_delete(iid):
            deleted.append(iid)
            alive.pop(iid, None)
            return True, ""

        self._patch(core, "list_instances", fake_list)
        self._patch(core, "delete_instance", fake_delete)
        self._patch(core, "_still_there", lambda iid: iid in alive)

        # User clicks emergency delete while meta.iid is still empty!
        r = self.api.emergency_delete(None)
        self.assertEqual(r, {"ok": True, "gone": True})
        self.assertEqual(deleted, [701])
        self.assertIn(999, alive, "Foreign instance must NOT be touched")
        self.assertEqual(rental_journal.get(run_id)["state"], rental_journal.DELETED)
        self.assertEqual(rental_journal.unresolved(), [])

    # 5. App restarted after rental created (no cancel!) -> recovers iid WITHOUT deleting live session
    def test_05_restart_after_crash_mid_rent_recovers_iid_without_deleting_live_session(self):
        run_id = "crashrun0005"
        rental_journal.mark_creating(run_id, offer_id=10)
        deleted = []
        self._patch(core, "list_instances", lambda: [{"id": 702, "label": f"vastgame:{run_id}"}])
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))

        res = rental_journal.reconcile_pending(core_mod=core, polls=1, step=0.0)
        self.assertEqual(res[run_id]["state"], rental_journal.CREATED)
        self.assertEqual(res[run_id]["iid"], 702)
        self.assertEqual(deleted, [], "Live user session must NEVER be auto-deleted without cancel_requested")
        self.assertEqual(rental_journal.get(run_id)["iid"], 702)

    # 6. Instance appears in list_instances() with eventual-consistency delay
    def test_06_delayed_instance_appearance_found_by_bounded_retries_and_deleted(self):
        run_id = "delayed00006"
        rental_journal.mark_creating(run_id, offer_id=10)
        rental_journal.mark_cancel_requested(run_id)
        polls = {"n": 0}
        alive = set()
        deleted = []

        def delayed_list():
            polls["n"] += 1
            if polls["n"] == 3:
                alive.add(703)
            return [{"id": iid, "label": f"vastgame:{run_id}"} for iid in sorted(alive)]

        def fake_delete(iid):
            deleted.append(iid)
            alive.discard(iid)
            return True, ""

        self._patch(core, "list_instances", delayed_list)
        self._patch(core, "delete_instance", fake_delete)
        self._patch(core, "_still_there", lambda iid: iid in alive)

        res = rental_journal.reconcile_run(run_id, core_mod=core, polls=4, step=0.0, allow_delete=True)
        self.assertTrue(res["deleted"])
        self.assertEqual(res["iid"], 703)
        self.assertEqual(deleted, [703])
        self.assertEqual(rental_journal.get(run_id)["state"], rental_journal.DELETED)

    # 7. API returns temporary errors several times, then succeeds
    def test_07_temporary_api_errors_recover_on_subsequent_poll(self):
        run_id = "flakyapi0007"
        rental_journal.mark_creating(run_id, offer_id=10)
        rental_journal.mark_cancel_requested(run_id)
        calls = {"n": 0}
        alive = {704}
        deleted = []

        def flaky_list():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise VastgameError("503 temporary", key="vast.http")
            return [{"id": iid, "label": f"vastgame:{run_id}"} for iid in sorted(alive)]

        def fake_delete(iid):
            deleted.append(iid)
            alive.discard(iid)
            return True, ""

        self._patch(core, "list_instances", flaky_list)
        self._patch(core, "delete_instance", fake_delete)
        self._patch(core, "_still_there", lambda iid: iid in alive)

        res = rental_journal.reconcile_run(run_id, core_mod=core, polls=4, step=0.0, allow_delete=True)
        self.assertTrue(res["deleted"])
        self.assertEqual(deleted, [704])

    # 8. User simultaneously has other rentals -> untouched
    def test_08_concurrent_foreign_rentals_are_never_deleted(self):
        run_id = "foreign00008"
        rental_journal.mark_creating(run_id, offer_id=10)
        rental_journal.mark_cancel_requested(run_id)
        deleted = []
        self._patch(core, "list_instances", lambda: [
            {"id": 801, "label": "vastgame"},
            {"id": 802, "label": "vastgame:other_run"},
            {"id": 803, "label": "user-jupyter"},
        ])
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))

        res = rental_journal.reconcile_run(run_id, core_mod=core, polls=2, step=0.0, allow_delete=True)
        self.assertFalse(res["deleted"])
        self.assertEqual(res["state"], rental_journal.UNCERTAIN)
        self.assertEqual(deleted, [], "No foreign instance may ever be deleted")

    # 9. Multiple instances found with the same label -> refuses blind deletion, marks UNCERTAIN
    def test_09_multiple_instances_same_label_refuses_auto_delete(self):
        run_id = "dup000000009"
        rental_journal.mark_creating(run_id, offer_id=10)
        rental_journal.mark_cancel_requested(run_id)
        deleted = []
        self._patch(core, "list_instances", lambda: [
            {"id": 901, "label": f"vastgame:{run_id}"},
            {"id": 902, "label": f"vastgame:{run_id}"},
        ])
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))

        res = rental_journal.reconcile_run(run_id, core_mod=core, polls=2, step=0.0, allow_delete=True)
        self.assertFalse(res["deleted"])
        self.assertEqual(res["state"], rental_journal.UNCERTAIN)
        self.assertEqual(res["reason"], "multiple_matches")
        self.assertEqual(res["matched_iids"], [901, 902])
        self.assertEqual(deleted, [])

    # 10. Instance deleted manually by user -> reconciliation confirms and marks DELETED
    def test_10_manually_deleted_instance_reconciles_to_deleted(self):
        run_id = "manual000010"
        rental_journal.mark_created(run_id, 1010)
        rental_journal.mark_cancel_requested(run_id, iid=1010)
        self._patch(core, "_still_there", lambda iid: False)

        res = rental_journal.reconcile_run(run_id, core_mod=core, polls=1, step=0.0, allow_delete=True)
        self.assertTrue(res["deleted"])
        self.assertEqual(res["reason"], "already_gone")
        self.assertEqual(rental_journal.get(run_id)["state"], rental_journal.DELETED)

    # 11. Repeat reconciliation after already-confirmed deletion is idempotent
    def test_11_repeat_reconcile_after_deleted_is_noop(self):
        run_id = "repeat000011"
        rental_journal.mark_deleted(run_id, iid=1111, reason="confirmed_deleted")
        delete_mock = mock.Mock()
        self._patch(core, "delete_instance", delete_mock)

        res = rental_journal.reconcile_run(run_id, core_mod=core, polls=2, step=0.0, allow_delete=True)
        self.assertTrue(res["deleted"])
        self.assertEqual(res["state"], rental_journal.DELETED)
        delete_mock.assert_not_called()

    # 12 & 13. Worker ignores SIGTERM and tries to overwrite journal after cancel_requested
    def test_12_and_13_sigterm_ignoring_worker_cannot_rollback_journal(self):
        stubborn = subprocess.Popen(["bash", "-c", "trap '' TERM; while true; do sleep 0.05; done"])
        self.addCleanup(lambda: (stubborn.kill(), stubborn.wait()))

        j = jobs.FileJob("play", pid=stubborn.pid, args={"offer_id": 10})
        run_id = j.run_id
        rental_journal.mark_creating(run_id, offer_id=10)

        aborted = jobs.abort_active()
        self.assertTrue(aborted["aborted"])
        self.assertTrue(rental_journal.get(run_id)["cancel_requested"])

        # Stale worker tries to mark_created / mark_completed after abort -> MUST NOT clear cancel_requested or state!
        rental_journal.mark_created(run_id, 1212)
        rental_journal.mark_completed(run_id, iid=1212)
        entry = rental_journal.get(run_id)
        self.assertTrue(entry["cancel_requested"])
        self.assertEqual(entry["state"], rental_journal.CANCEL_REQUESTED)
        self.assertEqual(entry["iid"], 1212, "Late iid is preserved for deletion without clearing cancel_requested")

    # 14. New rental starts after previous uncertain rental -> previous entry is NOT overwritten
    def test_14_new_rental_preserves_previous_unresolved_journal_entry(self):
        rental_journal.mark_creating("old_run_14", offer_id=1)
        rental_journal.mark_uncertain("old_run_14", reason="rent_response_lost")

        rental_journal.mark_prepared("new_run_14", offer_id=2)
        rental_journal.mark_created("new_run_14", 1402)
        rental_journal.mark_completed("new_run_14", iid=1402)

        unresolved = rental_journal.unresolved()
        self.assertEqual([e["run_id"] for e in unresolved], ["old_run_14"])
        self.assertEqual(rental_journal.get("new_run_14")["state"], rental_journal.COMPLETED)

    # 15 & 16. Offline at startup keeps warning; network recovery reconciles & deletes cancelled rental
    def test_15_and_16_offline_startup_preserves_warning_then_network_recovery_cleans_up(self):
        run_id = "offline00015"
        rental_journal.mark_creating(run_id, offer_id=10)
        rental_journal.mark_cancel_requested(run_id)

        # App starts while offline
        self._patch(core, "status", mock.Mock(side_effect=VastgameError("offline", key="vast.net")))
        self._patch(core, "balance", mock.Mock(side_effect=VastgameError("offline", key="vast.net")))
        st_offline = self.api.state()
        self.assertIn("error", st_offline)
        self.assertEqual([e["run_id"] for e in st_offline["unresolved_rentals"]], [run_id])

        # Network recovers: instance 1515 with vastgame:<run_id> is found & deleted on next state()
        alive = {1515}
        deleted = []
        self._patch(core, "status", lambda: {"state": "none", "kind": "none", "machine": None, "links": None, "archives": {}, "progs": {}})
        self._patch(core, "balance", lambda: 10.0)
        self._patch(core, "list_instances", lambda: [{"id": iid, "label": f"vastgame:{run_id}"} for iid in sorted(alive)])
        self._patch(core, "delete_instance", lambda iid: (deleted.append(iid) or True, "") if not alive.discard(iid) else (True, ""))
        self._patch(core, "_still_there", lambda iid: iid in alive)

        st_online = self.api.state()
        self.assertNotIn("error", st_online)
        # Wait for single-flight background reconciliation triggered by state() to complete
        deadline = time.monotonic() + 3.0
        while self.api._reconcile_running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(deleted, [1515])
        self.assertEqual(self.api.state()["unresolved_rentals"], [])
        self.assertEqual(rental_journal.get(run_id)["state"], rental_journal.DELETED)

    # 17. Repeat emergency_delete does not affect other user machines
    def test_17_repeat_emergency_delete_does_not_touch_other_machines(self):
        alive = {1701, 1702}
        deleted = []
        self._patch(core, "list_instances", lambda: [{"id": iid} for iid in sorted(alive)])
        self._patch(core, "delete_instance", lambda iid: (deleted.append(iid) or True, "") if not alive.discard(iid) else (True, ""))

        r1 = self.api.emergency_delete(1701)
        self.assertEqual(r1, {"ok": True, "gone": True})
        r2 = self.api.emergency_delete(1701)
        self.assertEqual(r2["error"]["key"], "core.emergency_mismatch")
        self.assertEqual(deleted, [1701])
        self.assertEqual(alive, {1702})

    # 18. Corrupted journal file does not crash or delete any unconfirmed instance
    def test_18_corrupted_journal_refuses_unconfirmed_deletion_and_surfaces_warning(self):
        rental_journal.FILE.write_text("{broken json!!!", encoding="utf-8")
        deleted = []
        self._patch(core, "list_instances", lambda: [{"id": 1801, "label": "vastgame"}])
        self._patch(core, "delete_instance", lambda iid: deleted.append(iid) or (True, ""))

        unres = rental_journal.unresolved()
        self.assertTrue(any(e["reason"] == "journal_corrupted" for e in unres))
        r = self.api.emergency_delete(None)
        self.assertEqual(r["error"]["key"], "core.instance_unconfirmed")
        self.assertEqual(deleted, [])

    # 19. Stage H: Slow reconciliation API must NOT block gui.state() and must deduplicate concurrent calls
    def test_19_state_reconciliation_is_non_blocking_and_single_flight(self):
        run_id = "slowrec00019"
        rental_journal.mark_creating(run_id, offer_id=10)
        rental_journal.mark_cancel_requested(run_id)

        entered = threading.Event()
        release = threading.Event()
        reconcile_calls = {"n": 0}

        def slow_reconcile_pending(**kw):
            reconcile_calls["n"] += 1
            entered.set()
            release.wait(timeout=3.0)
            return {}

        self._patch(core, "status", lambda: {"state": "none", "kind": "none", "machine": None, "links": None, "archives": {}, "progs": {}})
        self._patch(core, "balance", lambda: 10.0)
        self._patch(rental_journal, "reconcile_pending", slow_reconcile_pending)

        t0 = time.monotonic()
        st1 = self.api.state()
        dt = time.monotonic() - t0
        self.assertLess(dt, 0.5, "gui.state() must return immediately without blocking on slow reconciliation")
        self.assertTrue(entered.wait(timeout=2.0), "background reconciliation should start")
        self.assertEqual([e["run_id"] for e in st1["unresolved_rentals"]], [run_id])

        # Second concurrent state() and manual reconcile_rentals() must NOT spawn duplicate concurrent reconciliations
        self.api.state()
        r_manual = self.api.reconcile_rentals()
        self.assertTrue(r_manual["reconciling"])
        self.assertEqual(reconcile_calls["n"], 1, "single-flight lock must prevent duplicate concurrent reconciliations")

        release.set()
        deadline = time.monotonic() + 2.0
        while self.api._reconcile_running and time.monotonic() < deadline:
            time.sleep(0.01)

    # 20. Stage H: Visual dismiss hides UI card but keeps technical reconciliation active until Vast confirms absence
    def test_20_dismiss_hides_ui_banner_but_preserves_technical_reconciliation(self):
        run_id = "dismiss00020"
        rental_journal.mark_creating(run_id, offer_id=10)
        rental_journal.mark_cancel_requested(run_id)

        self.api.dismiss_unresolved_rental(run_id)
        # Visual UI list hides the dismissed entry
        self.assertEqual(rental_journal.unresolved(), [])
        # Technical journal still tracks it as CANCEL_REQUESTED (NOT DELETED!)
        entry = rental_journal.get(run_id)
        self.assertEqual(entry["state"], rental_journal.CANCEL_REQUESTED)
        self.assertTrue(entry["dismissed"])
        self.assertEqual([e["run_id"] for e in rental_journal.unresolved(include_dismissed=True)], [run_id])

        # When instance appears on Vast.ai, technical reconciliation STILL discovers and deletes it!
        alive = {2020}
        deleted = []
        self._patch(core, "list_instances", lambda: [{"id": iid, "label": f"vastgame:{run_id}"} for iid in sorted(alive)])
        self._patch(core, "delete_instance", lambda iid: (deleted.append(iid) or True, "") if not alive.discard(iid) else (True, ""))
        self._patch(core, "_still_there", lambda iid: iid in alive)

        res = rental_journal.reconcile_pending(core_mod=core, polls=1, step=0.0)
        self.assertTrue(res[run_id]["deleted"])
        self.assertEqual(deleted, [2020])
        self.assertEqual(rental_journal.get(run_id)["state"], rental_journal.DELETED)


if __name__ == "__main__":
    unittest.main()

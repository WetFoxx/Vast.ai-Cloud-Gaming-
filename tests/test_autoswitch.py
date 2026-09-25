# Автозамена машины, которую Vast не запустил (у KVM часть хостов виснет в Create/Loading):
# фоновый процесс удаляет зависшую и берёт следующую из списка человека — не больше MAX_TRIES.
# Vast и ядро поддельные: без денег и сети.

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jobs    # noqa: E402
import market  # noqa: E402
from vastapi import VastgameError  # noqa: E402


def offer(oid, mid):
    return {"id": oid, "machine_id": mid, "gpu_name": f"RTX {oid}", "geolocation": "Japan, JP", "dph_total": 0.2}


OFFERS = {1: offer(1, 101), 2: offer(2, 102), 3: offer(3, 103), 4: offer(4, 104), 5: offer(5, 101)}


class Run(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.rented, self.alive, self.stuck, self.gone_offers = [], set(), set(), set()
        self.cancel_on = None
        self.job = jobs.Job("play")
        self.job.meta = {}
        self.job.set_meta = lambda **kw: self.job.meta.update(kw)
        for obj, name, value in [
            (market, "BAD_HOSTS", tmp / "bad.json"), (jobs.settings, "FILE", tmp / "settings.json"),
            (jobs.core, "template", lambda: {"hash_id": "h", "recommended_disk_space": 100}),
            (jobs.core, "require_no_machine", lambda: None),
            (jobs.core, "offer_by_id", self.offer_by_id),
            (jobs.core, "rent", self.rent),
            (jobs.core, "rental_body", lambda tpl, size, sync=None: {}),
            (jobs.core, "wait_ready", self.wait_ready),
            (jobs.core, "identity_notes", lambda r: []),
            (jobs.core, "_still_there", lambda iid: iid in self.alive),
            (jobs.time, "sleep", lambda s: None)]:
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def offer_by_id(self, oid, size):
        if oid in self.gone_offers or oid not in OFFERS:
            raise VastgameError("занята", key="core.offer_gone", id=oid)
        return dict(OFFERS[oid])

    def rent(self, oid, body, o):
        self.rented.append(oid)
        iid = 1000 + oid
        self.alive.add(iid)
        return iid, 0

    def wait_ready(self, iid, t0, o, ui):
        if self.cancel_on == o["id"]:
            raise VastgameError("отменён", key="core.start_cancelled", id=iid)
        if o["id"] in self.stuck:                 # как core._failed_start: пометить хост, удалить, «try_another»
            market.mark_bad(o["machine_id"], "Vast не запустил машину за 10 минут.")
            self.alive.discard(iid)
            raise VastgameError("другую", key="core.try_another")
        return {"links": {"moonlight": "m"}, "minutes": 5, "boot": (0, "ok", ""), "identity": None}

    def play(self, spare=(2, 3, 4)):
        jobs.run_play(self.job, 1, spare=list(spare))
        return self.job

    def test_first_ok_no_switch(self):
        self.assertIsNone(self.play().error)
        self.assertEqual(self.rented, [1])

    def test_stuck_twice_third_starts(self):
        self.stuck = {1, 2}
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 2, 3])
        self.assertEqual(job.meta["tried"], [101, 102])
        self.assertEqual(job.meta["iid"], 1003)                      # отмена и продолжение — про новую машину
        self.assertEqual([e["params"]["n"] for e in job.events if e["key"] == "core.switching"], [2, 3])

    def test_gives_up_after_max_tries(self):
        self.stuck = {1, 2, 3, 4}
        job = self.play()
        self.assertEqual(job.error["key"], "core.tries_exhausted")
        self.assertEqual(len(self.rented), jobs.MAX_TRIES)

    def test_taken_spare_skipped(self):
        self.stuck, self.gone_offers = {1}, {2}
        self.play()
        self.assertEqual(self.rented, [1, 3])

    def test_same_host_and_marked_host_skipped(self):
        # 5 — другое предложение на том же хосте 101, что завис; 2 — хост помечен раньше
        self.stuck = {1}
        market.mark_bad(102, "раньше не запустилась")
        self.play(spare=(5, 2, 3))
        self.assertEqual(self.rented, [1, 3])

    def test_not_deleted_no_second_rent(self):
        # Зависшая не исчезла у Vast — вторую не берём: платили бы за две
        self.stuck = {1}
        with mock.patch.object(jobs.core, "_still_there", lambda iid: True), \
             mock.patch.object(jobs, "GONE_WAIT", 0):
            job = self.play()
        self.assertEqual(job.error["key"], "core.switch_blocked")
        self.assertEqual(self.rented, [1])

    def test_cancel_is_not_retried(self):
        self.cancel_on = 1
        job = self.play()
        self.assertEqual((job.error["key"], self.rented), ("core.start_cancelled", [1]))

    def test_no_spare_left(self):
        self.stuck = {1}
        job = self.play(spare=())
        self.assertEqual((job.error["key"], self.rented), ("core.no_spare", [1]))

    def test_resume_then_switch(self):
        # Mac выключался во время запуска машины 1; после продолжения она зависла — берём следующую
        self.job.meta = {"iid": 1001, "t0": 0, "offer": dict(OFFERS[1])}
        self.alive.add(1001)
        with mock.patch.object(jobs.core, "list_instances", lambda: [{"id": 1001}]):
            self.stuck = {1}
            self.play()
        self.assertEqual(self.rented, [2])


class TestMarketKvm(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for name, value in (("BAD_HOSTS", tmp / "bad.json"), ("STARTS", tmp / "starts.json")):
            p = mock.patch.object(market, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_vm_deverified_hosts_hidden(self):
        base = {"gpu_name": "RTX 3060", "dph_total": 0.1, "reliability": 0.99, "inet_up_cost": 0, "inet_down_cost": 0}
        offers = [dict(base, id=1, machine_id=1, is_vm_deverified=False),
                  dict(base, id=2, machine_id=2, is_vm_deverified=True),
                  dict(base, id=3, machine_id=3, verification="deverified")]   # общая отметка — не причина прятать
        with mock.patch.object(market, "call", lambda *a, **k: {"offers": offers}):
            self.assertEqual([o["id"] for o in market.search(100)], [1, 3])

    def test_start_times_remembered(self):
        self.assertIsNone(market.start_minutes(7))
        for s in (180, 240, 3000, 200, 220, 260):          # одна долгая попытка медиану не портит
            market.record_start(7, s)
        self.assertEqual(market.start_minutes(7), 4)
        base = {"gpu_name": "RTX 3060", "dph_total": 0.1, "reliability": 0.99, "id": 1, "machine_id": 7}
        with mock.patch.object(market, "call", lambda *a, **k: {"offers": [base]}):
            self.assertEqual(market.search(100)[0]["_start_min"], 4)


if __name__ == "__main__":
    unittest.main()

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


OFFERS = {1: offer(1, 101), 2: offer(2, 102), 3: offer(3, 103), 4: offer(4, 104), 5: offer(5, 101), 6: offer(6, 106)}
NEVER = None


class Race(unittest.TestCase):
    """Забег: сразу до RACE машин, остаётся первая дошедшая до running, остальные удаляются тут же."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.clock = 1000.0
        self.run_after = {}          # offer id → через сколько секунд после аренды running (None — никогда)
        self.broken = set()          # offer id, которые Vast сразу показывает exited
        self.rented, self.deleted, self.alive = [], [], {}
        self.rent_fails_on = None
        self.cancel_at = None
        self.at_wait = None
        self.job = jobs.Job("play")
        self.job.meta = {}
        self.job.set_meta = lambda **kw: self.job.meta.update(kw)
        self.job.cancelled = lambda: self.cancel_at is not None and self.clock >= self.cancel_at
        for obj, name, value in [
            (market, "BAD_HOSTS", tmp / "bad.json"), (market, "STARTS", tmp / "starts.json"),
            (jobs.settings, "FILE", tmp / "settings.json"),
            (jobs.core, "template", lambda: {"hash_id": "h", "recommended_disk_space": 100}),
            (jobs.core, "require_no_machine", lambda: None),
            (jobs.core, "offer_by_id", self.offer_by_id),
            (jobs.core, "rent", self.rent),
            (jobs.core, "rental_body", lambda tpl, size, sync=None, docker=False: {}),
            (jobs.core, "list_instances", self.list_instances),
            (jobs.core, "_delete_quietly", self.delete),
            (jobs.core, "wait_ready", self.wait_ready),
            (jobs.core, "identity_notes", lambda r: []),
            (jobs.core, "_still_there", lambda iid: iid in self.alive),
            (jobs.time, "time", lambda: self.clock),
            (jobs.time, "sleep", self.sleep)]:
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def sleep(self, s):
        self.clock += s

    def offer_by_id(self, oid, size, docker=False):
        if oid not in OFFERS:
            raise VastgameError("занята", key="core.offer_gone", id=oid)
        return dict(OFFERS[oid])

    def rent(self, oid, body, o):
        if oid == self.rent_fails_on:
            raise VastgameError("мало денег", key="core.rent_failed", msg="insufficient credit")
        self.rented.append(oid)
        iid = 1000 + oid
        self.alive[iid] = (oid, self.clock)
        return iid, self.clock

    def delete(self, iid):
        self.deleted.append(iid)
        self.alive.pop(iid, None)
        return True, None

    def list_instances(self):
        out = []
        for iid, (oid, t0) in self.alive.items():
            after = self.run_after.get(oid, NEVER)
            st = ("exited" if oid in self.broken else
                  "running" if after is not None and self.clock - t0 >= after else "loading")
            out.append({"id": iid, "actual_status": st, "status_msg": ""})
        return out

    def wait_ready(self, iid, t0, o, ui, docker=False):
        self.at_wait = (iid, set(self.alive))      # что было живо, когда начали готовить победителя
        return {"links": {"moonlight": "m"}, "minutes": 5, "boot": (0, "ok", ""), "identity": None}

    def play(self, spare=(2, 3, 4, 6)):
        jobs.run_play(self.job, 1, spare=list(spare))
        return self.job

    def keys(self):
        return [e["key"] for e in self.job.events if e["key"] and e["key"].startswith("core.race")]

    def test_docker_no_nvenc_winner_replaced_by_next(self):
        # Вкладка Docker: флаг доходит до проверки, аренды и ожидания; победитель без NVENC (wait_ready →
        # core.try_another) — новый забег из оставшихся, прежняя машина уже удалена
        self.run_after = {1: 30, 2: 60, 3: 90, 4: 30}
        seen, calls = [], []
        self.patch_many(("offer_by_id", lambda oid, size, docker=False: seen.append(docker) or self.offer_by_id(oid, size)),
                        ("rental_body", lambda tpl, size, sync=None, docker=False: {"docker": docker}))

        def wait_ready(iid, t0, o, ui, docker=False):
            calls.append((iid, docker))
            if len(calls) == 1:
                self.alive.pop(iid, None)                                  # _failed_start удалил её
                raise VastgameError("нет NVENC", key="core.try_another")
            return {"links": {"moonlight": "m"}, "minutes": 3, "boot": (0, "ok", ""), "identity": None}
        self.patch_many(("wait_ready", wait_ready))
        jobs.run_play(self.job, 1, spare=[2, 3, 4], docker=True)
        self.assertIsNone(self.job.error)
        self.assertTrue(seen and all(seen))
        self.assertEqual(calls[0], (1001, True))
        self.assertEqual(calls[1][1], True)
        self.assertNotEqual(calls[1][0], 1001)                             # другая машина

    def patch_many(self, *pairs):
        for name, value in pairs:
            p = mock.patch.object(jobs.core, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_fastest_wins_rest_deleted_before_setup(self):
        self.run_after = {1: NEVER, 2: 300, 3: 200}
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 2, 3])                       # сразу три
        self.assertEqual(self.at_wait, (1003, {1003}))                 # остальные удалены ДО настройки победителя
        self.assertEqual(sorted(self.deleted), [1001, 1002])
        self.assertEqual(market.bad_hosts(), {})                       # медленнее — не значит плохой
        self.assertEqual(job.meta["iid"], 1003)
        self.assertEqual(self.keys(), ["core.race_start", "core.race_won"])

    def test_stuck_ones_replaced(self):
        self.run_after = {4: 60}                                       # 1–3 висят, 4 быстрая
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 2, 3, 4, 6])                 # висящих заменили следующими
        self.assertEqual(self.at_wait[0], 1004)
        self.assertEqual(set(market.bad_hosts()), {"101", "102", "103"})
        self.assertIn("core.race_slow", self.keys())

    def test_everything_stuck_gives_up_and_deletes_all(self):
        job = self.play()
        self.assertEqual(job.error["key"], "core.tries_exhausted")
        self.assertLessEqual(len(self.rented), jobs.MAX_RENTS)
        self.assertEqual(self.alive, {})                               # ничего не осталось платить

    def test_broken_host_replaced_at_once(self):
        self.broken, self.run_after = {1}, {2: 900, 3: 900, 4: 30}
        self.play()
        self.assertEqual(self.rented[:4], [1, 2, 3, 4])                # 1 убрали сразу, не ждали 10 минут
        self.assertIn("core.race_broken", self.keys())
        self.assertEqual(self.at_wait[0], 1004)

    def test_cancel_deletes_every_machine(self):
        self.cancel_at = 1030
        job = self.play()
        self.assertEqual(job.error["key"], "core.start_cancelled")
        self.assertEqual((self.alive, sorted(self.deleted)), ({}, [1001, 1002, 1003]))

    def test_not_enough_balance_for_more(self):
        self.rent_fails_on, self.run_after = 2, {1: 120}
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1])                              # больше не пытались
        self.assertIn("core.race_rent_failed", self.keys())

    def test_first_rent_failure_is_reported(self):
        self.rent_fails_on = 1
        job = self.play(spare=())
        self.assertEqual(job.error["key"], "core.rent_failed")

    def test_same_host_not_twice(self):
        self.run_after = {1: 60}
        self.play(spare=(5, 2))                                         # 5 — тот же хост 101
        self.assertEqual(self.rented, [1, 2])

    def test_resume_race_after_restart(self):
        # Mac выключался во время забега: машины 1 и 2 уже арендованы — досмотреть, новых не брать
        for oid in (1, 2):
            self.alive[1000 + oid] = (oid, self.clock)
        self.job.meta = {"race": [{"iid": 1000 + o, "t0": self.clock, "offer": dict(OFFERS[o]), "errors": 0}
                                  for o in (1, 2)], "rents": 2, "used": [101, 102]}
        self.run_after = {2: 120, 3: 60}
        with mock.patch.object(jobs, "RACE", 2):
            job = self.play(spare=(3,))
        self.assertIsNone(job.error)
        self.assertEqual((self.rented, self.at_wait[0]), ([], 1002))


class TestLastMachine(unittest.TestCase):
    def test_short_sessions_are_not_last_machine(self):
        # Проигравшие забег удаляются через секунды — «последней машиной» остаётся та, на которой играли
        import history
        rows = [{"iid": 1, "machine_id": 11, "start": 1000, "end": 5000},
                {"iid": 2, "machine_id": 22, "start": 6000, "end": None},     # победитель, идёт сейчас
                {"iid": 3, "machine_id": 33, "start": 6001, "end": 6020}]     # проиграл забег
        with mock.patch.object(history, "_load", lambda: rows):
            self.assertEqual(history.last_machine()["machine_id"], 22)
            rows[1]["end"] = 7000
            self.assertEqual(history.last_machine(skip={"22"})["machine_id"], 11)


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

# Автозамена машины, которую Vast не запустил (у KVM часть хостов виснет в Create/Loading): фоновый процесс удаляет
# зависшую и берёт следующую из списка человека «не хуже» выбранной — не больше MAX_RENTS аренд.
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
    """Одна машина за раз (забег из трёх убран 2026-09-27): арендуется выбранная; Vast её не запустил (10 минут или
    ошибка хоста) — удалить и взять следующую из списка «не хуже» (видеокарту и пинг отбирает страница, здесь —
    не дороже по свежей цене, в Docker и трафик), всего MAX_RENTS аренд; таких нет — core.no_spare."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.clock = 1000.0
        self.run_after = {}          # offer id → через сколько секунд после аренды running (None — никогда)
        self.broken = set()          # offer id, которые Vast сразу показывает exited
        self.rented, self.deleted, self.alive, self.rent_times = [], [], {}, []
        self.rent_fails_on = None
        self.rent_gone = set()       # offer id, которые заняли прямо при аренде (Vast: no_such_ask)
        self.over = {}               # offer id → поля, отличающиеся от OFFERS (цена, трафик)
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
            (jobs.core, "rental_body", lambda tpl, size, sync=None, docker=False, lang=None: {}),
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
        return {**OFFERS[oid], **self.over.get(oid, {})}

    def rent(self, oid, body, o):
        if oid == self.rent_fails_on:
            raise VastgameError("мало денег", key="core.rent_failed", msg="insufficient credit")
        if oid in self.rent_gone:
            raise VastgameError("заняли", key="core.offer_gone", id=oid)
        self.rented.append(oid)
        self.rent_times.append(self.clock)
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
        self.at_wait = (iid, set(self.alive))      # что было живо, когда начали готовить машину
        return {"links": {"moonlight": "m"}, "minutes": 5, "boot": (0, "ok", ""), "identity": None}

    def play(self, spare=(2, 3, 4, 6), docker=False):
        jobs.run_play(self.job, 1, spare=list(spare), docker=docker)
        return self.job

    def keys(self):
        return [e["key"] for e in self.job.events if e["key"] and e["key"].startswith("core.race")]

    def patch_many(self, *pairs):
        for name, value in pairs:
            p = mock.patch.object(jobs.core, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_only_the_chosen_machine(self):
        self.run_after = {1: 60, 2: 10, 3: 10}                        # другие были бы быстрее — не важно
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual((self.rented, self.deleted), ([1], []))       # ровно одна, выбранная
        self.assertEqual(self.at_wait, (1001, {1001}))
        self.assertEqual(self.keys(), [])

    def test_stuck_one_replaced_by_next(self):
        self.run_after = {2: 60}                                       # 1 висит, 2 быстрая
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual((self.rented, self.deleted), ([1, 2], [1001]))
        self.assertEqual(self.at_wait, (1002, {1002}))                 # зависшая удалена до настройки замены
        self.assertEqual(set(market.bad_hosts()), {"101"})
        self.assertEqual(self.keys(), ["core.race_slow", "core.race_added"])
        self.assertGreaterEqual(self.rent_times[1] - self.rent_times[0], jobs.core.VAST_START_MIN * 60)

    def test_everything_stuck_gives_up_and_deletes_all(self):
        job = self.play()
        self.assertEqual(job.error["key"], "core.tries_exhausted")
        self.assertEqual(len(self.rented), jobs.MAX_RENTS)
        self.assertEqual(self.alive, {})                               # ничего не осталось платить

    def test_broken_host_replaced_at_once(self):
        self.broken, self.run_after = {1}, {2: 30}
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 2])
        self.assertLess(self.rent_times[1] - self.rent_times[0], 60)   # не ждали 10 минут
        self.assertIn("core.race_broken", self.keys())

    def test_cancel_deletes_the_machine(self):
        self.cancel_at = 1030
        job = self.play()
        self.assertEqual(job.error["key"], "core.start_cancelled")
        self.assertEqual((self.rented, self.alive, self.deleted), ([1], {}, [1001]))

    def test_chosen_taken_goes_back_to_list(self):
        # выбранную заняли, пока человек подтверждал, — не подсовывать другую, а вернуть к списку
        with mock.patch.dict(OFFERS, clear=False):
            del OFFERS[1]
            job = self.play()
        self.assertEqual(job.error["key"], "core.offer_gone")
        self.assertEqual(self.rented, [])

    def test_chosen_flagged_host_is_still_rented(self):
        # хост помечен «может не запуститься», но человек видел пометку и подтвердил — арендуем именно её
        market.mark_bad(101, "exited")
        self.run_after = {1: 60}
        self.play()
        self.assertEqual(self.rented, [1])

    def test_replacement_not_more_expensive(self):
        self.over = {2: {"dph_total": 0.25}, 3: {"dph_total": 0.2}}   # 2 подорожала, 3 — как выбранная
        self.run_after = {2: 30, 3: 30}
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 3])

    def test_replacement_a_bit_more_expensive_is_fine(self):
        # [2026-10-15] «похожая»: до +20% к цене выбранной — берём (раньше — только не дороже, и замен не оставалось)
        self.over = {2: {"dph_total": 0.22}}
        self.run_after = {2: 30}
        job = self.play(spare=(2,))
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 2])
        self.assertEqual(jobs.MAX_RENTS, 5)

    def test_docker_replacement_traffic_not_higher(self):
        self.over = {1: {"inet_up_cost": 0.001}, 2: {"inet_up_cost": 0.002}, 3: {"inet_down_cost": 0.001}}
        self.run_after = {2: 30, 3: 30}
        job = self.play(docker=True)
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 3])

    def test_no_replacement_left_goes_back_to_list(self):
        self.over = {2: {"dph_total": 0.3}}                            # единственная запасная — дороже
        job = self.play(spare=(2,))
        self.assertEqual(job.error["key"], "core.no_spare")
        self.assertEqual((self.rented, self.alive), ([1], {}))
        # Этап «Vast» — один раз (первая аренда): замены нет, и экран не должен «вернуться» на первый этап
        self.assertEqual([e["key"] for e in job.events if e["key"] == "stage.vast"], ["stage.vast"])

    def test_replacement_taken_while_renting_skips_to_next(self):
        self.rent_gone, self.run_after = {2}, {3: 30}
        job = self.play()
        self.assertIsNone(job.error)
        self.assertEqual(self.rented, [1, 3])

    def test_not_enough_balance_for_replacement(self):
        self.rent_fails_on = 2
        job = self.play()
        self.assertEqual(job.error["key"], "core.rent_failed")
        self.assertEqual(self.alive, {})                               # зависшая удалена, новой нет

    def test_first_rent_failure_is_reported(self):
        self.rent_fails_on = 1
        job = self.play(spare=())
        self.assertEqual(job.error["key"], "core.rent_failed")

    def test_same_host_not_twice(self):
        self.run_after = {2: 60}
        self.play(spare=(5, 2))                                         # 5 — тот же хост 101, что и зависшая 1
        self.assertEqual(self.rented, [1, 2])

    def test_resume_after_restart(self):
        # Mac выключался, пока Vast запускал выбранную: досмотреть её, новых не брать
        self.alive[1001] = (1, self.clock)
        self.job.meta = {"race": [{"iid": 1001, "t0": self.clock, "offer": dict(OFFERS[1]), "errors": 0}],
                         "rents": 1, "used": [101], "limit": {"price": 0.2, "traffic": 0}}
        self.run_after = {1: 120}
        job = self.play(spare=(3,))
        self.assertIsNone(job.error)
        self.assertEqual((self.rented, self.at_wait[0]), ([], 1001))

    def test_docker_no_nvenc_replaced_by_next(self):
        # Вкладка Docker: машина без NVENC (wait_ready → core.try_another) — замена следующей «не хуже»,
        # прежняя уже удалена
        self.run_after = {1: 30, 2: 60, 3: 90}
        seen, calls = [], []
        self.patch_many(("offer_by_id", lambda oid, size, docker=False: seen.append(docker) or self.offer_by_id(oid, size)),
                        ("rental_body", lambda tpl, size, sync=None, docker=False, lang=None: {"docker": docker}))

        def wait_ready(iid, t0, o, ui, docker=False):
            calls.append((iid, docker))
            if len(calls) == 1:
                self.alive.pop(iid, None)                                  # _failed_start удалил её
                raise VastgameError("нет NVENC", key="core.try_another")
            return {"links": {"moonlight": "m"}, "minutes": 3, "boot": (0, "ok", ""), "identity": None}
        self.patch_many(("wait_ready", wait_ready))
        jobs.run_play(self.job, 1, spare=[2, 3], docker=True)
        self.assertIsNone(self.job.error)
        self.assertTrue(seen and all(seen))
        self.assertEqual(calls, [(1001, True), (1002, True)])
        self.assertIn("core.race_added", self.keys())


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

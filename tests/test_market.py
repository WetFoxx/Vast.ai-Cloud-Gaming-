# market: последняя машина — ищется по номеру машины хоста среди свободных предложений (поддельный Vast).

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import market  # noqa: E402


def setUpModule():
    """Тесты не трогают настоящие файлы в папке настроек: настройки, историю, пометки хостов."""
    import tempfile
    import history, market, settings
    tmp = Path(tempfile.mkdtemp())
    for obj, name in [(settings, "FILE"), (history, "FILE"), (market, "BAD_HOSTS"), (market, "STARTS")]:
        p = mock.patch.object(obj, name, tmp / f"{obj.__name__}.json")
        p.start()
        unittest.addModuleCleanup(p.stop)

OFFER = {"id": 41118946, "machine_id": 222, "gpu_name": "RTX 3060", "dph_total": 0.12, "reliability": 0.95,
         "inet_up_cost": 0.001, "inet_down_cost": 0.001}


class TestLastMachine(unittest.TestCase):
    def setUp(self):
        self.queries = []
        for obj, name, value in [(market, "bad_hosts", lambda: {}),
                                 (market.history, "last_machine",
                                  lambda skip=(): {"machine_id": 222, "gpu": "RTX 3060", "country": "Ohio, US", "start": 5})]:
            p = mock.patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)

    def fake_call(self, offers):
        def call(method, path, body=None, **k):
            self.queries.append(body)
            return {"offers": offers}
        return mock.patch.object(market, "call", call)

    def test_free(self):
        with self.fake_call([OFFER]):
            last = market.last_machine(100)
        self.assertEqual(last["offer"]["id"], 41118946)                  # надёжность 0.95 не мешает — выбор человека
        q = self.queries[0]
        self.assertEqual(q["machine_id"], {"eq": 222})
        self.assertEqual(q["vms_enabled"], {"eq": True})                 # остальные фильтры (KVM, диск) на месте
        self.assertEqual(q["allocated_storage"], 100)

    def test_failed_host_is_flagged_not_hidden(self):
        with mock.patch.object(market, "bad_hosts", lambda: {"222": {"reason": "exited"}}), self.fake_call([OFFER]):
            rows = market.search(100)
        self.assertEqual(len(rows), 1)                                   # не спрятан
        self.assertEqual(rows[0]["_failed"], "exited")                   # а помечен

    def test_permanent_failure_hidden_for_a_while(self):
        # [2026-10-15] экраном владеет хост (68380, дважды) / нет выхода для захвата (152536, дважды) — не показывать;
        # через HIDE_DAYS дней — снова с пометкой; прочие сбои — только пометка
        import datetime
        today = datetime.date.today()
        old = (today - datetime.timedelta(days=market.HIDE_DAYS)).isoformat()
        bad = {"1": {"reason": "На этой машине не работает видео: экран не запускается: Screen(s) found, but none "
                               "have a usable configuration. no screens found", "date": today.isoformat()},
               "2": {"reason": "видео: нет выхода на монитор: обычный захват тоже не видит экран", "date": today.isoformat()},
               "3": {"reason": "нет выхода на монитор: обычный захват тоже не видит экран", "date": old},
               # прежняя надпись образа — бывала и без библиотеки кодировщика (152536), это образ теперь лечит
               "6": {"reason": "нет выхода на монитор: хост не дал видеокарте экран", "date": today.isoformat()},
               "4": {"reason": "NVENC не работает: unsupported device", "date": today.isoformat()}}
        offers = [dict(OFFER, id=i, machine_id=i) for i in (1, 2, 3, 4, 5, 6)]
        with mock.patch.object(market, "bad_hosts", lambda: bad), self.fake_call(offers):
            rows = {o["id"]: o for o in market.search(100)}
        self.assertEqual(sorted(rows), [3, 4, 5, 6])
        self.assertTrue(rows[3]["_failed"] and rows[4]["_failed"] and rows[6]["_failed"])       # срок вышел / временный сбой — с пометкой

    def test_slow_disk_hidden(self):
        slow = dict(OFFER, id=1, disk_bw=69.2)                             # как у хоста в Португалии
        fast = dict(OFFER, id=2, disk_bw=2743.0)
        unknown = dict(OFFER, id=3)                                      # Vast не сообщил скорость — не прячем
        with self.fake_call([slow, fast, unknown]):
            ids = {o["id"] for o in market.search(100)}
        self.assertEqual(ids, {2, 3})

    def test_slow_internet_hidden(self):
        # [2026-10-15] Гонконг, 17 Мбит/с — драйвер, клиент Steam и сохранения качались десятки минут
        slow = dict(OFFER, id=1, inet_down=17.2)
        fast = dict(OFFER, id=2, inet_down=758)
        edge = dict(OFFER, id=3, inet_down=100)
        unknown = dict(OFFER, id=4)                                      # Vast не сообщил — не прячем
        with self.fake_call([slow, fast, edge, unknown]):
            ids = {o["id"] for o in market.search(100)}
        self.assertEqual(ids, {2, 3, 4})

    def test_small_memory_hidden(self):
        # 7.7 ГБ — как у двух машин в Японии, что замолкали при скачивании; 15.5 — это «16-гиговая» (часть у хоста)
        small = dict(OFFER, id=1, cpu_ram=7864)
        sixteen = dict(OFFER, id=2, cpu_ram=15872)
        big = dict(OFFER, id=3, cpu_ram=64000)
        unknown = dict(OFFER, id=4)                                      # Vast не сообщил — не прячем
        with self.fake_call([small, sixteen, big, unknown]):
            ids = {o["id"] for o in market.search(100)}
        self.assertEqual(ids, {2, 3, 4})

    def test_busy(self):
        with self.fake_call([]):
            last = market.last_machine(100)
        self.assertIsNone(last["offer"])
        self.assertEqual(last["gpu"], "RTX 3060")

    def test_never_played(self):
        with mock.patch.object(market.history, "last_machine", lambda skip=(): None), self.fake_call([OFFER]):
            self.assertIsNone(market.last_machine(100))
        self.assertEqual(self.queries, [])                               # лишний запрос не делается


if __name__ == "__main__":
    unittest.main()

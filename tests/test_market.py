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

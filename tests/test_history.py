# history: своя запись сессий и сведение с реальными списаниями Vast (поддельными).

import sys
import tempfile
import time
import json
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import history  # noqa: E402

TREE = {"results": [
    {"type": "instance", "source": "instance-7", "start": 1000, "amount": 0.25, "metadata": {"label": "vastgame"}, "items": [
        {"type": "gpu", "description": "1.2 hours at $0.200/hour", "amount": 0.24},
        {"type": "disk", "description": "1.3 hours at $0.004/hour", "amount": 0.005},
        {"type": "bwu", "description": "0.3 GB Uploaded at $0.003/GB", "amount": 0.001}]},
    {"type": "instance", "source": "instance-8", "start": 2000, "amount": 0.0, "items": []},     # не запустилась
    {"type": "instance", "source": "instance-5", "start": 500, "amount": 2.0, "metadata": {"label": "ml-job"}, "items": []},
    {"type": "instance", "source": "instance-9", "start": 3000, "amount": 0.1, "metadata": {"label": "vastgame"}, "items": [
        {"type": "gpu", "description": "0.5 hours at $0.200/hour", "amount": 0.1}]}],
    "next_token": None}


class TestHistory(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(history, "FILE", Path(tempfile.mkdtemp()) / "sessions.json")
        p.start()
        self.addCleanup(p.stop)
        self.key = "ключ-1"
        p = mock.patch.object(history, "read_key", lambda: self.key)     # настоящую Связку ключей не трогаем
        p.start()
        self.addCleanup(p.stop)

    def test_last_machine(self):
        self.assertIsNone(history.last_machine())
        history.started(7, {"gpu_name": "RTX 3090", "machine_id": 111}, t0=100)
        history.started(8, {"gpu_name": "RTX 3060", "machine_id": 222}, t0=200)
        history.started(9, {}, t0=300)                                      # машина неизвестна — пропустить
        self.assertEqual(history.last_machine()["machine_id"], 222)
        self.assertEqual(history.last_machine(skip={"222"})["machine_id"], 111)   # хост в чёрном списке

    def test_local_sessions(self):
        history.started(7, {"gpu_name": "RTX 3090", "geolocation": "Latvia, LV", "dph_total": 0.2}, t0=100)
        self.assertEqual(history.current(7)["gpu"], "RTX 3090")
        history.ended(7, when=200)
        self.assertIsNone(history.current(7))
        history.started(9, {}, t0=300)
        history.reconcile(running_ids=set())          # машину удалили на сайте Vast
        self.assertIsNone(history.current(9))
        self.assertTrue(history._load()[-1]["closed_outside"])

    def vast(self, days_back=40):
        """Поддельный Vast: по записи на машину 5 за каждые сутки UTC, $1 в сутки; фильтр day — как у Vast."""
        today = int(time.time()) // 86400 * 86400
        recs = [{"type": "instance", "source": "instance-5", "start": today - d * 86400, "amount": 1.0,
                 "metadata": {"label": "vastgame"}, "items": []} for d in range(days_back)]
        asked = []

        def call(method, path, params=None, base=None):
            f = json.loads(params["select_filters"])["day"]
            asked.append(f["gte"])
            return {"results": [r for r in recs if f["gte"] <= r["start"] <= f["lte"]]}
        return call, asked, recs

    def test_settled_days_from_memory(self):
        # [0.4.5] Второй раз из Vast — только последние сутки; итог тот же
        call, asked, recs = self.vast()
        with mock.patch.object(history, "call", call):
            first = history.charges(30)[5]["amount"]
            asked.clear()
            recs[0]["amount"] = 5.0                      # сегодняшние сутки ещё меняются — их берём свежими
            second = history.charges(30)[5]["amount"]
        self.assertEqual(first, 30.0)
        self.assertEqual(second, 34.0)
        self.assertEqual(len(asked), 1)
        self.assertGreaterEqual(asked[0], int(time.time()) // 86400 * 86400 - history.SETTLED)
        with mock.patch.object(history, "call", call):
            self.assertEqual(history.charges(7)[5]["amount"], 11.0)       # короче — тоже из памяти

    def test_longer_period_or_other_account_refetched(self):
        call, asked, recs = self.vast(100)
        with mock.patch.object(history, "call", call):
            history.charges(7)
            asked.clear()
            self.assertEqual(history.charges(30)[5]["amount"], 30.0)     # в памяти только 7 дней — целиком
            self.assertLess(asked[0], int(time.time()) - 29 * 86400)
            asked.clear()
            self.key = "ключ-2"                                           # другой аккаунт Vast
            history.charges(7)
            self.assertLess(asked[0], int(time.time()) - 6 * 86400)
        self.assertNotIn("ключ", history._charges_file().read_text())     # в памяти — отпечаток, не ключ

    def test_summary_merges_charges(self):
        history.started(7, {"gpu_name": "RTX 3090", "geolocation": "Latvia, LV", "dph_total": 0.2}, t0=900,
                        game={"appid": 1245620, "name": "ELDEN RING", "account": "7656"})
        with mock.patch.object(history, "call", lambda *a, **k: TREE):
            s = history.summary(30)
        self.assertEqual(s["sessions"][1]["game"], {"appid": "1245620", "name": "ELDEN RING"})   # без аккаунта
        self.assertIsNone(s["sessions"][0]["game"])                                           # не знаем — None
        self.assertEqual([x["iid"] for x in s["sessions"]], [9, 7])       # свежие сверху, пустая скрыта
        seven = s["sessions"][1]
        self.assertEqual((seven["gpu"], seven["hours"], seven["traffic_cost"]), ("RTX 3090", 1.2, 0.001))
        self.assertEqual(s["totals"]["count"], 2)
        self.assertEqual(s["first"], 900)                                 # реальное начало периода
        self.assertAlmostEqual(s["totals"]["amount"], 0.35)
        self.assertAlmostEqual(s["totals"]["hours"], 1.7)
        self.assertEqual(s["other"], {"count": 1, "amount": 2.0})           # чужая машина — отдельно
        with mock.patch.object(history, "call", lambda *a, **k: TREE):
            everything = history.summary(30, all_machines=True)
        self.assertEqual(everything["totals"]["count"], 3)


if __name__ == "__main__":
    unittest.main()

# history: своя запись сессий и сведение с реальными списаниями Vast (поддельными).

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import history  # noqa: E402

TREE = {"results": [
    {"type": "instance", "source": "instance-7", "start": 1000, "amount": 0.25, "items": [
        {"type": "gpu", "description": "1.2 hours at $0.200/hour", "amount": 0.24},
        {"type": "disk", "description": "1.3 hours at $0.004/hour", "amount": 0.005},
        {"type": "bwu", "description": "0.3 GB Uploaded at $0.003/GB", "amount": 0.001}]},
    {"type": "instance", "source": "instance-8", "start": 2000, "amount": 0.0, "items": []},     # не запустилась
    {"type": "instance", "source": "instance-9", "start": 3000, "amount": 0.1, "items": [
        {"type": "gpu", "description": "0.5 hours at $0.200/hour", "amount": 0.1}]}],
    "next_token": None}


class TestHistory(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(history, "FILE", Path(tempfile.mkdtemp()) / "sessions.json")
        p.start()
        self.addCleanup(p.stop)

    def test_local_sessions(self):
        history.started(7, {"gpu_name": "RTX 3090", "geolocation": "Latvia, LV", "dph_total": 0.2}, t0=100)
        self.assertEqual(history.current(7)["gpu"], "RTX 3090")
        history.ended(7, when=200)
        self.assertIsNone(history.current(7))
        history.started(9, {}, t0=300)
        history.reconcile(running_ids=set())          # машину удалили на сайте Vast
        self.assertIsNone(history.current(9))
        self.assertTrue(history._load()[-1]["closed_outside"])

    def test_summary_merges_charges(self):
        history.started(7, {"gpu_name": "RTX 3090", "geolocation": "Latvia, LV", "dph_total": 0.2}, t0=900)
        with mock.patch.object(history, "call", lambda *a, **k: TREE):
            s = history.summary(30)
        self.assertEqual([x["iid"] for x in s["sessions"]], [9, 7])       # свежие сверху, пустая скрыта
        seven = s["sessions"][1]
        self.assertEqual((seven["gpu"], seven["hours"], seven["traffic_cost"]), ("RTX 3090", 1.2, 0.001))
        self.assertEqual(s["totals"]["count"], 2)
        self.assertAlmostEqual(s["totals"]["amount"], 0.35)
        self.assertAlmostEqual(s["totals"]["hours"], 1.7)


if __name__ == "__main__":
    unittest.main()

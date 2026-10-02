# [0.4.5] parallel.run — независимые запросы одновременно: результаты по порядку, ошибка — первая по порядку
# (VastgameError — подвид SystemExit — тоже доходит), время ≈ самый долгий вызов.

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import parallel  # noqa: E402
from vastapi import VastgameError  # noqa: E402


def slow(value, sec=0.3):
    def f():
        time.sleep(sec)
        return value
    return f


class TestRun(unittest.TestCase):
    def test_order_and_time(self):
        t0 = time.time()
        self.assertEqual(parallel.run(slow(1), slow(2), slow(3)), [1, 2, 3])
        self.assertLess(time.time() - t0, 0.6)          # по очереди — 0.9 с

    def test_single_and_empty(self):
        self.assertEqual(parallel.run(lambda: 5), [5])
        self.assertEqual(parallel.run(), [])

    def test_first_error_by_order(self):
        def late():
            time.sleep(0.2)
            raise VastgameError("первая по порядку", key="a")

        def early():
            raise ValueError("быстрее, но вторая")
        with self.assertRaises(VastgameError) as c:      # SystemExit-подвид не теряется в потоке
            parallel.run(late, early)
        self.assertEqual(c.exception.key, "a")


if __name__ == "__main__":
    unittest.main()

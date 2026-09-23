# notify.low_balance: одно уведомление на каждое падение ниже $1; после пополнения — снова.

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify  # noqa: E402


class TestLowBalance(unittest.TestCase):
    def setUp(self):
        self.sent = []
        for obj, name, value in [(notify.settings, "FILE", Path(tempfile.mkdtemp()) / "settings.json"),
                                 (notify, "send", lambda title, text: self.sent.append((title, text)))]:
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_once_until_topped_up(self):
        self.assertFalse(notify.low_balance(3.0))
        self.assertTrue(notify.low_balance(0.9, price=0.2, lang="ru"))
        self.assertIn("4.5 ч", self.sent[0][1])                  # 0.9 / 0.2 — на сколько часов хватит
        self.assertFalse(notify.low_balance(0.7, lang="ru"))     # уже предупредили
        self.assertFalse(notify.low_balance(5.0))                # пополнили
        self.assertTrue(notify.low_balance(0.5, lang="en"))      # снова ниже — снова предупредить
        self.assertEqual(len(self.sent), 2)
        self.assertIn("before your next session", self.sent[1][1])   # машины нет — другой текст

    def test_language_falls_back_to_last_window_language(self):
        notify.settings.set("ui_language", "es")
        notify.low_balance(0.5)
        self.assertIn("Recarga", self.sent[0][1])


if __name__ == "__main__":
    unittest.main()

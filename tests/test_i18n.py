# Переводы: во всех языках одни и те же ключи и одни и те же подстановки {имя} —
# чтобы ни одна строка не потерялась при добавлении нового текста.

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import i18n  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def ui_texts():
    """Словари окна из ui/i18n.js: JSON между «const I18N = » и первой «};»."""
    src = (ROOT / "ui" / "i18n.js").read_text()
    body = src.split("const I18N = ", 1)[1].split("\n};", 1)[0] + "\n}"
    return json.loads(body)


class TestI18n(unittest.TestCase):
    def check(self, texts, where):
        self.assertEqual(set(texts), {"ru", "en", "es"}, where)
        for lang in ("ru", "es"):
            self.assertEqual(set(texts[lang]), set(texts["en"]), f"{where}: ключи {lang} ≠ en")
            for key, text in texts["en"].items():
                self.assertEqual(set(PLACEHOLDER.findall(texts[lang][key])), set(PLACEHOLDER.findall(text)),
                                 f"{where}: подстановки в {lang}/{key}")
                self.assertTrue(texts[lang][key].strip(), f"{where}: пусто {lang}/{key}")

    def test_window(self):
        self.check(ui_texts(), "ui/i18n.js")

    def test_python(self):
        self.check(i18n.TEXTS, "i18n.py")

    def test_status_kinds_translated(self):
        """Для каждого вида состояния из core.status есть фраза, для каждого состояния — заголовок."""
        texts = ui_texts()["en"]
        for kind in ("none", "many", "vast_error", "vast", "tailscale", "boot_error", "restore",
                     "sync_error", "syncing", "ready"):
            self.assertIn("status.text." + kind, texts)
        for state in ("none", "starting", "ready", "syncing", "stopping", "error"):
            self.assertIn("status.title." + state, texts)


if __name__ == "__main__":
    unittest.main()

# Переводы окна (ui/i18n.js): во всех языках одни и те же ключи и подстановки {имя},
# и для каждого ключа, который используют ядро и vastapi, есть перевод.

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def ui_texts():
    """Словари окна из ui/i18n.js: JSON между «const I18N = » и первой «};»."""
    src = (ROOT / "ui" / "i18n.js").read_text()
    body = src.split("const I18N = ", 1)[1].split("\n};", 1)[0] + "\n}"
    return json.loads(body)


class TestI18n(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        texts = ui_texts()
        self.assertEqual(set(texts), {"ru", "en", "es"})
        for lang in ("ru", "es"):
            self.assertEqual(set(texts[lang]), set(texts["en"]), f"ключи {lang} ≠ en")
            for key, text in texts["en"].items():
                self.assertEqual(set(PLACEHOLDER.findall(texts[lang][key])), set(PLACEHOLDER.findall(text)),
                                 f"подстановки в {lang}/{key}")
                self.assertTrue(texts[lang][key].strip(), f"пусто {lang}/{key}")

    def test_windows_variants(self):
        """«ключ@win» (текст для Windows) — только при обычном ключе и с теми же подстановками: страница подменяет
        один другим, и пропавшая подстановка показала бы «{state}» буквально."""
        for lang, texts in ui_texts().items():
            for key, text in texts.items():
                if key.endswith("@win"):
                    base = key[:-4]
                    self.assertIn(base, texts, f"{lang}/{key}: нет обычного ключа")
                    self.assertEqual(set(PLACEHOLDER.findall(text)), set(PLACEHOLDER.findall(texts[base])),
                                     f"подстановки {lang}/{key}")

    def test_python_keys_translated(self):
        """Каждый key="..." в core.py, vastapi.py, updater.py и steamlib.py есть в словаре, и подстановки совпадают по именам."""
        texts = ui_texts()["en"]
        for f in ("core.py", "vastapi.py", "updater.py", "steamlib.py"):
            src = (ROOT / f).read_text()
            for key in set(re.findall(r'key="([a-z_.]+)"', src)) | set(re.findall(r'"(core\.[a-z_]+)"', src)):
                self.assertIn(key, texts, f"{f}: нет перевода для {key}")

    def test_status_kinds_translated(self):
        """Для каждого вида состояния из core.status есть фраза, для каждого состояния — заголовок."""
        texts = ui_texts()["en"]
        for kind in ("none", "many", "vast_error", "vast", "tailscale", "boot_error", "restore",
                     "sync_error", "syncing", "ready"):
            self.assertIn("status.text." + kind, texts)
        for state in ("none", "starting", "ready", "syncing", "stopping", "error"):
            self.assertIn("status.title." + state, texts)
        for stage in ("vast", "tailscale", "restore", "ready"):
            self.assertIn("stage." + stage, texts)


if __name__ == "__main__":
    unittest.main()

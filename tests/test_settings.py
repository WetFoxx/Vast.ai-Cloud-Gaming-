# tests/test_settings.py — проверка надёжности и атомарности сохранения настроек (S1, S2)
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings


class TestSettingsAtomic(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_file = settings.FILE
        settings.FILE = Path(self.temp_dir.name) / "settings.json"

    def tearDown(self):
        settings.FILE = self.orig_file
        self.temp_dir.cleanup()

    def test_basic_load_and_set(self):
        self.assertEqual(settings.load(), {})
        settings.set("lang", "ru")
        self.assertEqual(settings.get("lang"), "ru")
        settings.set("lang", None)
        self.assertIsNone(settings.get("lang"))

    def test_s1_temporary_replace_failure_recovers(self):
        """S1: Первые попытки os.replace завершаются PermissionError, последующая succeeds.
        Итоговый JSON полный и правильный; прямая перезапись файла не используется."""
        settings.set("initial_canary", "intact")
        attempts = 0
        real_replace = os.replace

        def flaky_replace(src, dst):
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise PermissionError(13, "Access is denied (temporary lock)")
            return real_replace(src, dst)

        with patch("os.replace", side_effect=flaky_replace):
            settings.set("new_key", "success_value")

        self.assertGreaterEqual(attempts, 3)
        data = settings.load()
        self.assertEqual(data.get("initial_canary"), "intact")
        self.assertEqual(data.get("new_key"), "success_value")

        # Проверяем, что временный файл очищен
        tmp_files = list(Path(self.temp_dir.name).glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_s2_permanent_replace_failure_preserves_file(self):
        """S2: Все попытки замены завершаются ошибкой.
        Ошибка сообщается вызывающему коду; SHA-256 исходного settings.json не изменился;
        файл читается и содержит прежние настройки."""
        settings.set("vital_canary", "must_not_be_corrupted")
        initial_bytes = settings.FILE.read_bytes()
        initial_sha = hashlib.sha256(initial_bytes).hexdigest()

        def always_fail_replace(src, dst):
            raise PermissionError(13, "Persistent access denied")

        with patch("os.replace", side_effect=always_fail_replace):
            with self.assertRaises(OSError) as cm:
                settings.set("vital_canary", "corrupted_value")
            self.assertIn("Persistent access denied", str(cm.exception))

        # SHA-256 исходного файла побайтово не изменился
        current_bytes = settings.FILE.read_bytes()
        current_sha = hashlib.sha256(current_bytes).hexdigest()
        self.assertEqual(current_sha, initial_sha)

        # Файл читается и содержит прежние настройки
        data = settings.load()
        self.assertEqual(data.get("vital_canary"), "must_not_be_corrupted")

        # Временный файл не остался
        tmp_files = list(Path(self.temp_dir.name).glob("*.tmp"))
        self.assertEqual(tmp_files, [])


if __name__ == "__main__":
    unittest.main()

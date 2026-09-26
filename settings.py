# settings.py — настройки утилиты на этом компьютере (не секретные): язык окна и т.п.
# Лежат в settings.json в папке настроек: macOS — ~/Library/Application Support/vastgame,
# Windows — %APPDATA%\vastgame. Ключи и токены здесь НЕ хранятся — они в Keychain.

import json
import os
import sys
import time
from pathlib import Path

LANGUAGES = ("ru", "en", "es")


def config_dir():
    """Папка настроек утилиты: macOS — Application Support, Windows — %APPDATA%, иначе ~/.config."""
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "vastgame"
    elif os.name == "nt":
        d = Path(os.environ.get("APPDATA", Path.home())) / "vastgame"
    else:
        d = Path.home() / ".config" / "vastgame"
    d.mkdir(parents=True, exist_ok=True)
    return d


FILE = config_dir() / "settings.json"


def load():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}


def get(name, default=None):
    return load().get(name, default)


def set(name, value):   # noqa: A001 — «set» здесь понятнее всего
    data = load()
    if value is None:
        data.pop(name, None)
    else:
        data[name] = value
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def applog(text):
    """Строка в app.log (папка настроек): ход связки Moonlight и запуска трансляции — чтобы разобрать сбой у другого
    человека по одному присланному файлу. Без секретов: ни паролей, ни PIN."""
    try:
        log = config_dir() / "app.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            log.unlink()
        with open(log, "a") as f:
            f.write(f"{time.strftime('%F %T')} {text}\n")
    except OSError:
        pass

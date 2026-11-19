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
    """Папка настроек утилиты: macOS — Application Support, Windows — %APPDATA%, иначе ~/.config.
    VASTGAME_CONFIG_DIR — отдельная папка для проверочной сборки, чтобы не трогать обычные настройки."""
    override = os.environ.get("VASTGAME_CONFIG_DIR")
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
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
        return json.loads(FILE.read_text(encoding="utf-8"))
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
    # Целиком или никак: сбой посередине записи (на Windows без режима UTF-8 — на кириллице, 2026-10-08) стирал файл
    tmp = FILE.with_name(f"{FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    last_err = None
    for attempt in range(5):
        try:
            os.replace(tmp, FILE)
            return
        except OSError as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    if last_err:
        raise last_err


def applog(text):
    """Строка в app.log (папка настроек): ход связки Moonlight и запуска трансляции — чтобы разобрать сбой у другого
    человека по одному присланному файлу. Без секретов: ни паролей, ни PIN. Во время автотестов — никуда (тесты, которым
    журнал нужен, подменяют эту функцию): иначе они пишут в настоящий app.log этого Mac (2026-10-09 — 45 строк)."""
    if "unittest" in sys.modules:                 # как защита Связки ключей: приложение unittest не загружает
        return
    try:
        log = config_dir() / "app.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            log.unlink()
        with open(log, "a") as f:
            f.write(f"{time.strftime('%F %T')} {text}\n")
    except OSError:
        pass

# paths.py — где искать программы (rclone, tailscale, brew, ssh).
# Приложение, запущенное из Finder или Dock, получает короткий PATH без папок Homebrew,
# поэтому кроме PATH смотрим в обычные места установки на Mac и Windows.

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)     # запущено из собранного vastgame.app (PyInstaller)
# Программы, которые едут внутри приложения (сейчас — rclone): папка bin рядом с кодом
BUNDLED_BIN = Path(getattr(sys, "_MEIPASS", HERE)) / "bin"

EXTRA = {
    "darwin": ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"],
    "win32": [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), d)
              for d in ("Tailscale", "rclone")],
}
# Tailscale для Mac — приложение; его программа для командной строки лежит внутри .app
APP_BINARIES = {"tailscale": ["/Applications/Tailscale.app/Contents/MacOS/Tailscale"]}


def self_cmd(*args):
    """Команда, которой приложение запускает само себя с параметрами (фоновые операции, автозапуск):
    в собранном .app — его исполняемый файл, из папки проекта — Python с app_main.py."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, str(HERE / "app_main.py"), *args]


def find(name):
    """Полный путь к программе name или None. Своя копия внутри приложения — важнее системной."""
    bundled = BUNDLED_BIN / (name + (".exe" if sys.platform == "win32" else ""))
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    p = shutil.which(name)
    if p:
        return p
    for d in EXTRA.get(sys.platform, []):
        p = shutil.which(name, path=d)
        if p:
            return p
    for p in APP_BINARIES.get(name, []) if sys.platform == "darwin" else []:
        if os.path.exists(p):
            return p
    return None


def need(name):
    """Путь к программе или просто имя (тогда запуск даст понятную ошибку «не найдено»)."""
    return find(name) or name

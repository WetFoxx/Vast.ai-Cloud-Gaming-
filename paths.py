# paths.py — где искать программы (rclone, tailscale, brew, ssh).
# Приложение, запущенное из Finder или Dock, получает короткий PATH без папок Homebrew,
# поэтому кроме PATH смотрим в обычные места установки на Mac и Windows.

import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def run(cmd, timeout, env=None):
    """Запустить программу и дождаться не дольше timeout секунд — что бы она ни делала.

    Вывод пишется во временные файлы, а не в каналы: если программа запустит что-то ещё
    (приложение Tailscale при первом запуске так делает) и оно унаследует вывод, мы всё равно не
    ждём его. Не уложилась — завершаем её саму (не трогая то, что она запустила) и бросаем
    subprocess.TimeoutExpired. Возвращает CompletedProcess с текстом stdout/stderr."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env)
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            raise
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(cmd, p.returncode, out.read().decode(errors="replace"),
                                           err.read().decode(errors="replace"))


TAILSCALE_TIMEOUT = 8   # с: дольше Tailscale не отвечает только когда он не настроен или завис


def tailscale_status():
    """«tailscale status --json» как словарь; None — Tailscale не установлен, не отвечает или
    ответил не тем (на свежей установке, пока его не настроили, так бывает)."""
    exe = find("tailscale")
    if not exe:
        return None
    try:
        return json.loads(run([exe, "status", "--json"], TAILSCALE_TIMEOUT).stdout or "null") or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

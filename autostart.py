# autostart.py — служба macOS, которая продолжает прерванный запуск или завершение игры после
# включения компьютера, даже если окно vastgame не открывать.
#
# Это LaunchAgent: файл ~/Library/LaunchAgents/com.vastgame.resume.plist. macOS запускает
# «python jobs.py resume» при входе в систему и затем раз в 5 минут. Команда быстрая: смотрит
# job.json и, только если операция прервалась (процесса нет, а она не закончена), продолжает её.
# Ничего не арендует сама: запуск продолжается, только если машина уже была арендована.
#
# Ставится с экрана «Настройка» (шаг «Продолжать после перезагрузки»). Убрать — кнопкой там же
# или вручную: launchctl bootout gui/$(id -u)/com.vastgame.resume и удалить файл.
# Windows — позже (Планировщик заданий).

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from settings import config_dir

LABEL = "com.vastgame.resume"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
HERE = Path(__file__).resolve().parent
INTERVAL = 300   # с: как часто macOS запускает проверку (кроме входа в систему)


def wanted():
    """Каким должен быть файл службы: этот Python и этот jobs.py (после сборки .app пути другие)."""
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(HERE / "jobs.py"), "resume"],
        "WorkingDirectory": str(HERE),
        "RunAtLoad": True,
        "StartInterval": INTERVAL,
        # Продолжение идёт отдельным процессом в своей сессии — не убивать его вместе с проверкой
        "AbandonProcessGroup": True,
        "StandardOutPath": str(config_dir() / "job.log"),
        "StandardErrorPath": str(config_dir() / "job.log"),
    }


def status():
    """"on" — служба стоит и актуальна; "outdated" — стоит, но пути устарели; "off" — нет."""
    if sys.platform != "darwin":
        return "unsupported"
    try:
        current = plistlib.loads(PLIST.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return "off"
    return "on" if current == wanted() else "outdated"


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def install():
    """Поставить (или обновить) службу и сразу включить. True — получилось."""
    if sys.platform != "darwin":
        return False
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")          # была старая — выгрузить (ошибка, если не было, — не важна)
    PLIST.write_bytes(plistlib.dumps(wanted()))
    r = _launchctl("bootstrap", domain, str(PLIST))
    return r.returncode == 0 or loaded()


def uninstall():
    if sys.platform != "darwin":
        return False
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    PLIST.unlink(missing_ok=True)
    return True


def loaded():
    """Служба загружена в launchd (работает по расписанию)."""
    return _launchctl("print", f"gui/{os.getuid()}/{LABEL}").returncode == 0


def refresh():
    """Служба стоит, но пути устарели (проект переехал, собрали .app) — переписать. Её однажды
    включил сам человек, поэтому обновить без вопроса можно; выключенную — не трогаем."""
    if status() == "outdated":
        install()

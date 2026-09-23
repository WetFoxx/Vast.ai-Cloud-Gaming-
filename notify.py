# notify.py — системные уведомления: «машина готова», «игра закончена», «не получилось».
# Нужны, потому что запуск и завершение идут в фоне и окно в это время может быть закрыто.
# Тексты — из ui/i18n.js, на языке окна (его передаёт страница).
#
# Mac: osascript «display notification». Пока приложение не собрано в .app, уведомление приходит
# от имени «Редактор скриптов» (Script Editor); в первый раз macOS может спросить разрешение.

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def texts(lang):
    """Словарь переводов нужного языка (JSON внутри ui/i18n.js — как в tests/test_i18n.py); запасной — en."""
    src = (HERE / "ui" / "i18n.js").read_text()
    all_ = json.loads(src.split("const I18N = ", 1)[1].split("\n};", 1)[0] + "\n}")
    return {**all_["en"], **all_.get(lang or "en", {})}


def t(tr, key, **params):
    """Перевод с подстановками {имя}, как t() на странице (списки — через запятую)."""
    text = tr.get(key, key)
    for k, v in params.items():
        text = text.replace("{" + k + "}", ", ".join(map(str, v)) if isinstance(v, list) else str(v))
    return text


def send(title, text):
    """Показать уведомление. Ошибки глушим: уведомление не должно ломать запуск или завершение."""
    if sys.platform != "darwin":
        return   # Windows — позже (шаг сборки)
    # Тексты передаём аргументами (argv), а не вставляем в скрипт — кавычки в тексте ничего не сломают
    script = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]
    cmd = ["osascript"] + [a for line in script for a in ("-e", line)] + [title, text]
    try:
        subprocess.run(cmd, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def job_finished(kind, result, error, lang):
    """Уведомление о конце запуска (kind="play") или завершения ("stop")."""
    tr = texts(lang)
    if error:
        detail = t(tr, error["key"], **error.get("params", {})) if error.get("key") in tr else error.get("text", "")
        title = t(tr, "notify.play_failed.title" if kind == "play" else "notify.stop_failed.title")
        send(title, f"{detail}\n{t(tr, 'notify.details')}".strip())
    elif kind == "play":
        send(t(tr, "notify.ready.title"), t(tr, "notify.ready.text", min=result.get("minutes", "?")))
    else:
        text = t(tr, "notify.stopped.text") if result.get("deleted") else t(tr, "core.delete_pending", id=result.get("iid"))
        send(t(tr, "notify.stopped.title"), text)

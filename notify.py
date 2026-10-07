# notify.py — системные уведомления: «машина готова», «игра закончена», «не получилось».
# Нужны, потому что запуск и завершение идут в фоне и окно в это время может быть закрыто.
# Тексты — из ui/i18n.js, на языке окна (его передаёт страница).
#
# Mac: osascript «display notification». Пока приложение не собрано в .app, уведомление приходит
# от имени «Редактор скриптов» (Script Editor); в первый раз macOS может спросить разрешение.

import json
import os
import subprocess
import sys
from pathlib import Path

import paths
import settings

HERE = Path(__file__).resolve().parent
LOW_BALANCE = 1.0   # $: ниже — уведомление «баланс заканчивается»


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


# Windows: уведомление средствами самой Windows (WinRT) из PowerShell 5.1, который есть в любой Windows 10/11.
# Тексты — переменными окружения: в скрипт не вставляются, кавычки и теги в них ничего не сломают (Escape).
# Пока у приложения нет ярлыка в «Пуске» со своим AppUserModelID, уведомление подписано именем PowerShell.
WIN_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
WIN_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$t = [Security.SecurityElement]::Escape($env:VG_TITLE)
$b = [Security.SecurityElement]::Escape($env:VG_TEXT)
$a = if ($env:VG_SOUND -eq '1') { '<audio src="ms-winsoundevent:Notification.Default"/>' } else { '<audio silent="true"/>' }
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual>$a</toast>")
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:VG_APPID).Show(
    [Windows.UI.Notifications.ToastNotification]::new($x))
"""


def _send_windows(title, text, sound):
    env = {**os.environ, "VG_TITLE": title, "VG_TEXT": text, "VG_SOUND": "1" if sound else "0", "VG_APPID": WIN_APP_ID}
    paths.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", WIN_TOAST],
              20, env=env)


def send(title, text, sound=False):
    """Показать уведомление. Ошибки глушим: уведомление не должно ломать запуск или завершение.
    sound — со звуком (вопрос машины: его должны заметить и во время игры)."""
    if sys.platform == "win32":
        try:
            _send_windows(title, text, sound)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    if sys.platform != "darwin":
        return
    # Тексты передаём аргументами (argv), а не вставляем в скрипт — кавычки в тексте ничего не сломают
    show = "display notification (item 2 of argv) with title (item 1 of argv)" + (' sound name "Glass"' if sound else "")
    script = ["on run argv", show, "end run"]
    cmd = ["osascript"] + [a for line in script for a in ("-e", line)] + [title, text]
    try:
        paths.run(cmd, 10)
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
        text = (t(tr, "progress.stop_gone") if result.get("gone") else t(tr, "notify.stopped.text")
                if result.get("deleted") else t(tr, "core.delete_pending", id=result.get("iid")))
        send(t(tr, "notify.stopped.title"), text)


def language():
    """Язык уведомлений, когда окна нет: выбранный человеком, иначе последний язык окна."""
    return settings.get("language") or settings.get("ui_language") or "en"


def low_balance(credit, price=None, lang=None):
    """Баланс опустился ниже LOW_BALANCE — уведомить ОДИН раз; снова — только после пополнения.
    price — цена часа работающей машины (тогда в тексте — на сколько часов хватит).
    Зовут окно (при каждом обновлении) и служба автозапуска (раз в 5 минут). True — уведомили."""
    was = settings.get("low_balance_notified", False)
    if credit >= LOW_BALANCE:
        if was:
            settings.set("low_balance_notified", False)     # пополнили — в следующий раз снова предупредить
        return False
    if was:
        return False
    tr = texts(lang or language())
    money = f"${credit:.2f}"
    if price:
        text = t(tr, "notify.low_balance.playing", balance=money, hours=f"{credit / price:.1f}")
    else:
        text = t(tr, "notify.low_balance.idle", balance=money)
    send(t(tr, "notify.low_balance.title"), text)
    settings.set("low_balance_notified", True)
    return True

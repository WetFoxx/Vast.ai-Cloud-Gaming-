"""tray.py — значок VastGame в трее Windows (возле часов): закрытие окна прячет его, а не закрывает программу.

Стандартный NotifyIcon из WinForms (через pythonnet — им уже пользуется pywebview, новых библиотек не нужно). Создаётся
в потоке окна (Invoke на форме окна pywebview), его события приходят туда же. Щелчок — открыть окно; меню —
«Открыть VastGame» и «Выйти». Тексты — из ui/i18n.js на языке окна (tray.*). Mac — не используется (решение
2026-09-24: значка в строке меню нет)."""
import sys
from pathlib import Path

import notify
import settings


class Tray:
    def __init__(self, window, on_open, on_quit):
        self.window, self.on_open, self.on_quit = window, on_open, on_quit
        self.icon = None
        self.hint_shown = False

    def _lang(self):
        return settings.get("language") or settings.get("ui_language")

    def start(self):
        """Поставить значок (после загрузки окна: нужна его форма). Не вышло — окно закрывается как обычно."""
        try:
            from System import Action
            self.window.native.Invoke(Action(self._create))
        except Exception:                                  # noqa: BLE001
            settings.applog("трей: не удалось поставить значок")
            self.icon = None

    def _create(self):
        from System import EventHandler
        from System.Drawing import Icon
        from System.Windows.Forms import ContextMenuStrip, MouseButtons, NotifyIcon
        tr = notify.texts(self._lang())
        ico = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).parent / "assets" / "vastgame.ico"
        icon = NotifyIcon()
        icon.Icon = Icon(str(ico)) if ico.suffix == ".ico" else Icon.ExtractAssociatedIcon(str(ico))
        icon.Text = "VastGame"
        menu = ContextMenuStrip()
        menu.Items.Add(tr["tray.open"], None, EventHandler(lambda s, e: self.on_open()))
        menu.Items.Add(tr["tray.quit"], None, EventHandler(lambda s, e: self.on_quit()))
        icon.ContextMenuStrip = menu

        def click(s, e):
            if e.Button == MouseButtons.Left:
                self.on_open()
        icon.MouseClick += click
        icon.Visible = True
        self.icon = icon

    def hidden(self):
        """Окно спрятано: в первый раз — сказать, где теперь VastGame."""
        if self.hint_shown:
            return
        self.hint_shown = True
        tr = notify.texts(self._lang())
        notify.send(tr["tray.hidden.title"], tr["tray.hidden.text"])

    def stop(self):
        """Убрать значок (иначе он «висит» в трее до наведения мыши после выхода)."""
        icon, self.icon = self.icon, None
        if icon is None:
            return
        try:
            from System import Action

            def gone():
                icon.Visible = False
                icon.Dispose()
            self.window.native.Invoke(Action(gone))
        except Exception:                                  # noqa: BLE001
            pass

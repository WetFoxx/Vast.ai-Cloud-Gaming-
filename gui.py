# gui.py — окно приложения vastgame (pywebview): страница ui/index.html + функции Python для неё.
# Запуск: vastgame app          (VASTGAME_DEBUG=1 vastgame app — с инспектором страницы)
#
# Страница вызывает методы класса Api: window.pywebview.api.state() и т.п. Каждый вызов идёт
# в отдельном потоке, окно при этом не замирает. Ключи и токены в страницу не попадают никогда —
# только готовые данные: баланс, состояние машины, адреса.

import os
import webbrowser
from pathlib import Path

import webview   # pywebview: окно с веб-страницей (на Mac — системный WebKit, на Windows — Edge WebView2)

import core
from vastapi import VastgameError

HERE = Path(__file__).resolve().parent
LOW_BALANCE = 1.0   # $: ниже — предупреждение «баланс заканчивается»


class Api:
    """Функции для страницы. Возвращают словари — pywebview передаёт их в JavaScript как объекты."""

    def state(self):
        """Баланс и состояние машины для главного экрана."""
        try:
            st = core.status()
            credit = core.balance()
        except VastgameError as e:   # подвид SystemExit — ловим явно, иначе поток тихо завершится
            return {"error": str(e)}
        price = st["machine"]["price"] if st["machine"] else None
        return {
            "balance": credit,
            "low_balance": credit < LOW_BALANCE,
            "hours_left": credit / price if price else None,
            "status": st,
        }

    def open_url(self, url):
        """Открыть адрес в браузере — только наши адреса (кабинет Sunshine, страница статуса)."""
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)


def main():
    webview.create_window("vastgame", str(HERE / "ui" / "index.html"), js_api=Api(),
                          width=560, height=680, min_size=(460, 560))
    webview.start(debug=bool(os.environ.get("VASTGAME_DEBUG")))


if __name__ == "__main__":
    main()

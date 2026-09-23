# gui.py — окно приложения vastgame (pywebview): страница ui/index.html + функции Python для неё.
# Запуск: vastgame app          (VASTGAME_DEBUG=1 vastgame app — с инспектором страницы)
#
# Страница вызывает методы класса Api: window.pywebview.api.state() и т.п. Каждый вызов идёт
# в отдельном потоке, окно при этом не замирает. Ключи и токены в страницу не попадают никогда —
# только готовые данные: баланс, состояние машины, адреса.
#
# Долгие операции (аренда, позже — остановка) идут в фоне как Job: ядро (core.py) сообщает ему
# события, страница раз в секунду забирает их (progress) и показывает. Если ядру нужно «да/нет»,
# Job ждёт, пока человек ответит на странице (answer).

import os
import re
import threading
import time
import webbrowser
from pathlib import Path

import webview   # pywebview: окно с веб-страницей (на Mac — системный WebKit, на Windows — Edge WebView2)

import core
import market
import moonlight
import settings
import steamnames
from vastapi import VastgameError

HERE = Path(__file__).resolve().parent
LOW_BALANCE = 1.0   # $: ниже — предупреждение «баланс заканчивается»
OFFERS_SHOWN = 10   # сколько машин показывать в выборе


def error_data(e):
    """Ошибку — странице: ключ перевода и данные (перевод делает страница), плюс русский текст."""
    return {"key": getattr(e, "key", None), "params": getattr(e, "params", {}), "text": str(e)}


class Job:
    """Фоновая операция с машиной. Это же и «ui» для ядра: say/stage/archive/confirm/tailscale."""

    def __init__(self, kind):
        self.kind = kind                 # "play" или "stop"
        self.started = time.time()
        self.events = []                 # сообщения по порядку: {level, key, params, text}
        self.stage_key = None
        self.archives = {}               # {имя: [состояние, текст wolf]}
        self.question = None             # {id, key, params, text} — пока ждём ответа
        self.result = None
        self.error = None
        self.done = False
        self._lock = threading.Lock()
        self._answered = threading.Event()
        self._answer = False
        self._qid = 0

    # ---- ui для ядра
    def say(self, text, level="info", key=None, **params):
        with self._lock:
            self.events.append({"level": level, "key": key, "params": params, "text": text})

    def stage(self, key, text):
        with self._lock:
            self.stage_key = key
            self.events.append({"level": "stage", "key": "stage." + key, "params": {}, "text": text})

    def archive(self, name, state, text):
        with self._lock:
            self.archives[name] = [state, text]

    def confirm(self, question, key=None, **params):
        """Ждать ответа человека на странице (сколько угодно: без его «да» ничего не делаем)."""
        with self._lock:
            self._qid += 1
            self.question = {"id": self._qid, "key": key, "params": params, "text": question}
            self._answered.clear()
        self._answered.wait()
        with self._lock:
            self.question = None
            return self._answer

    def tailscale(self, text):
        url = re.search(r"https://\S+", text)
        with self._lock:
            self.events.append({"level": "tailscale", "key": None, "params": {}, "text": text,
                                "url": url.group(0) if url else None})

    # ---- для страницы
    def answer(self, qid, yes):
        with self._lock:
            if self.question and self.question["id"] == qid:
                self._answer = bool(yes)
                self._answered.set()

    def snapshot(self, since=0):
        with self._lock:
            return {"kind": self.kind, "elapsed": int(time.time() - self.started), "stage": self.stage_key,
                    "events": self.events[since:], "next": len(self.events), "archives": dict(self.archives),
                    "question": self.question, "result": self.result, "error": self.error, "done": self.done}


class Api:
    """Функции для страницы. Возвращают словари — pywebview передаёт их в JavaScript как объекты.
    Атрибуты с «_» pywebview странице не показывает."""

    def __init__(self):
        self._job = None
        self._names = {}          # {appid: название игры} — для сохранений pfx--<appid>
        self._window = None
        self._force_close = False

    # ---- настройки
    def settings(self):
        """Сохранённые настройки окна (язык — только если его выбрали сами)."""
        return {"language": settings.get("language")}

    def set_language(self, lang, remember=False):
        """Запомнить язык, выбранный человеком (иначе окно следует за языком системы)."""
        if remember and lang in settings.LANGUAGES:
            settings.set("language", lang)

    # ---- главный экран
    def state(self):
        """Баланс и состояние машины для главного экрана."""
        try:
            st = core.status()
            credit = core.balance()
        except VastgameError as e:   # подвид SystemExit — ловим явно, иначе поток тихо завершится
            return {"error": error_data(e)}
        price = st["machine"]["price"] if st["machine"] else None
        return {"balance": credit, "low_balance": credit < LOW_BALANCE,
                "hours_left": credit / price if price else None, "status": st,
                "game_names": self._game_names(st["archives"]), "busy": self._busy()}

    def _game_names(self, archive_names):
        missing = [n for n in archive_names if n.startswith("pfx--") and n[5:] not in self._names]
        if missing:
            self._names.update(steamnames.for_archives(missing))
        return self._names

    def _busy(self):
        return bool(self._job and not self._job.done)

    # ---- «Играть»: выбор машины
    def offers(self):
        """Подходящие машины (лучшие по полной цене часа), диск из шаблона и баланс."""
        try:
            core.require_no_machine()
            tpl = core.template()
            disk = core.template_disk(tpl)
            rows = market.search(disk)
            credit = core.balance()
        except VastgameError as e:
            return {"error": error_data(e)}
        return {"disk": disk, "min_rel": market.MIN_REL, "count": len(rows), "balance": credit,
                "offers": [{"id": o["id"], "gpu": o["gpu_name"], "country": market.country(o),
                            "price": o["dph_total"], "traffic": o["_traffic"], "total": o["_total"],
                            "reliability": o["reliability"]} for o in rows[:OFFERS_SHOWN]]}

    def check_offer(self, offer_id):
        """Свежие данные машины прямо перед подтверждением аренды."""
        try:
            disk = core.template_disk(core.template())
            o = core.offer_by_id(int(offer_id), disk)
            credit = core.balance()
        except VastgameError as e:
            return {"error": error_data(e)}
        return {"id": o["id"], "gpu": o["gpu_name"], "country": market.country(o), "price": o["dph_total"],
                "balance": credit, "hours": credit / o["dph_total"] if o["dph_total"] else None,
                "bad_reason": core.bad_host_reason(o)}

    def play(self, offer_id):
        """Арендовать машину (человек уже ответил «да») и следить за запуском в фоне."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        job = self._job = Job("play")

        def run():
            try:
                core.require_no_machine()                        # ещё раз — прямо перед арендой
                tpl = core.template()
                disk = core.template_disk(tpl)
                o = core.offer_by_id(int(offer_id), disk)       # свежая цена и наличие
                iid, t0 = core.rent(o["id"], core.rental_body(tpl, disk))
                r = core.wait_ready(iid, t0, o, job)
                job.result = {"links": r["links"], "minutes": r["minutes"], "boot": list(r["boot"]),
                              "notes": [{"level": lv, "key": k, "params": p, "text": tx}
                                        for lv, tx, k, p in core.identity_notes(r)]}
            except VastgameError as e:
                job.error = error_data(e)
            except Exception as e:                               # неожиданное — тоже показать, не молчать
                job.error = {"key": None, "params": {}, "text": f"{type(e).__name__}: {e}"}
            finally:
                job.done = True

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def stop(self):
        """«Закончить»: выгрузить всё, проверить, спросить про удаление (вопрос — на странице)."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        job = self._job = Job("stop")

        def run():
            try:
                r = core.stop(job)
                job.result = {"uploaded": r["uploaded"], "deleted": r["deleted"], "iid": r["iid"]}
            except VastgameError as e:
                job.error = error_data(e)
            except Exception as e:
                job.error = {"key": None, "params": {}, "text": f"{type(e).__name__}: {e}"}
            finally:
                job.done = True

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def emergency_delete(self, machine_id):
        """Удалить машину без выгрузки. Страница уже дважды спросила; удаляем только машину,
        которую человек видел (machine_id), и только когда не идёт другая операция."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        try:
            gone = core.emergency_delete(int(machine_id))
        except VastgameError as e:
            return {"error": error_data(e)}
        return {"ok": True, "gone": gone}

    def progress(self, since=0):
        """События текущей операции начиная с номера since."""
        if not self._job:
            return None
        snap = self._job.snapshot(int(since))
        snap["game_names"] = self._game_names(snap["archives"])
        return snap

    def answer(self, qid, yes):
        if self._job:
            self._job.answer(qid, yes)

    # ---- Moonlight
    def moonlight_apps(self, host):
        """Приложения Sunshine на машине и сохранённый выбор. installed=False — Moonlight не установлен."""
        if not moonlight.path():
            return {"installed": False}
        names = moonlight.apps(host) or [moonlight.DEFAULT_APP]
        saved = settings.get("moonlight_app")
        return {"installed": True, "apps": names,
                "selected": saved if saved in names else (moonlight.DEFAULT_APP if moonlight.DEFAULT_APP in names
                                                          else names[0])}

    def open_moonlight(self, host, app):
        """Открыть трансляцию; выбор приложения запомнить."""
        if not isinstance(host, str) or not host or not isinstance(app, str) or not app:
            return {"error": {"key": None, "params": {}, "text": "bad arguments"}}
        settings.set("moonlight_app", app)
        if not moonlight.stream(host, app):
            return {"error": {"key": "moonlight.missing", "params": {}, "text": "Moonlight не установлен"}}
        return {"ok": True}

    # ---- прочее
    def open_url(self, url):
        """Открыть адрес в браузере — только http(s): кабинет Sunshine, страница статуса, вход Tailscale."""
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)

    def force_close(self):
        """Человек подтвердил на странице: закрыть окно, хотя операция ещё идёт."""
        self._force_close = True
        self._window.destroy()

    def _on_closing(self):
        """Закрытие окна. Идёт операция — не закрывать молча, а спросить на странице.
        Спрашиваем страницей, а не системным диалогом: этот обработчик работает в главном потоке,
        и системный диалог из него повесил бы окно."""
        if self._busy() and not self._force_close:
            threading.Thread(target=lambda: self._window.evaluate_js("askClose()"), daemon=True).start()
            return False   # не закрывать
        return True


def main():
    api = Api()
    window = webview.create_window("vastgame", str(HERE / "ui" / "index.html"), js_api=api,
                                   width=620, height=760, min_size=(520, 600))
    api._window = window
    window.events.closing += api._on_closing
    webview.start(debug=bool(os.environ.get("VASTGAME_DEBUG")))


if __name__ == "__main__":
    main()

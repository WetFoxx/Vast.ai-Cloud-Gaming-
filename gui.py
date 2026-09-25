# gui.py — окно приложения vastgame (pywebview): страница ui/index.html + функции Python для неё.
# Запуск: vastgame app          (VASTGAME_DEBUG=1 vastgame app — с инспектором страницы)
#
# Страница вызывает методы класса Api: window.pywebview.api.state() и т.п. Каждый вызов идёт
# в отдельном потоке, окно при этом не замирает. Ключи и токены в страницу не попадают никогда —
# только готовые данные: баланс, состояние машины, адреса.
#
# «Играть» и «Закончить» идут в отдельном фоновом процессе (jobs.py): окно можно закрыть, операция
# доведётся до конца. Страница раз в секунду забирает её ход из файла (session_progress).
# Короткие операции (убрать игру, установка, вход в Google) — Job в потоке окна (progress, answer).

import functools
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import webview   # pywebview: окно с веб-страницей (на Mac — системный WebKit, на Windows — Edge WebView2)

import autostart
import cloudgames
import core
import history
import installer
import jobs
import market
import moonlight
import pairing
import wolfnode
import notify
import settings
import paths
import setup
import setupcheck
import sshkey
from vastapi import KEY_FILE
import steamnames
from jobs import Job, error_data
from vastapi import VastgameError, my_template

HERE = Path(__file__).resolve().parent
LOW_BALANCE = notify.LOW_BALANCE   # $: ниже — предупреждение «баланс заканчивается»
OFFERS_SHOWN = 10   # сколько машин показывать в выборе
DISK_MIN, DISK_MAX = jobs.DISK_MIN, jobs.DISK_MAX   # пределы ползунка диска, ГБ
# Установка программ через Homebrew (Mac). --cask — приложение, иначе — программа командной строки
BREW = {"rclone": ["rclone"], "tailscale_app": ["--cask", "tailscale-app"], "moonlight": ["--cask", "moonlight"]}


class Api:
    """Функции для страницы. Возвращают словари — pywebview передаёт их в JavaScript как объекты.
    Атрибуты с «_» pywebview странице не показывает."""

    def __init__(self):
        self._job = None
        self._names = {}          # {appid: название игры} — для сохранений pfx--<appid>
        self._window = None
        self._force_close = False
        self._ts_url = None       # ссылка входа Tailscale — только в памяти, чтобы открыть её ещё раз
        self._moonlight_lock = threading.Lock()   # «Запустить Moonlight» — не больше одного сразу

    # ---- настройки
    def settings(self):
        """Сохранённые настройки окна (язык — только если его выбрали сами)."""
        return {"language": settings.get("language")}

    def set_language(self, lang, remember=False):
        """Запомнить язык, выбранный человеком (иначе окно следует за языком системы)."""
        if remember and lang in settings.LANGUAGES:
            settings.set("language", lang)

    # ---- главный экран
    def state(self, lang=None):
        """Баланс и состояние машины для главного экрана. lang — язык окна (для уведомлений без окна)."""
        if lang in settings.LANGUAGES and settings.get("ui_language") != lang:
            settings.set("ui_language", lang)
        try:
            st = core.status()
            credit = core.balance()
        except VastgameError as e:   # подвид SystemExit — ловим явно, иначе поток тихо завершится
            return {"error": error_data(e)}
        price = st["machine"]["price"] if st["machine"] else None
        session = None
        try:
            history.reconcile({st["machine"]["id"]} if st["machine"] else set())
            cur = history.current(st["machine"]["id"]) if st["machine"] else None
            if cur:
                elapsed = int(time.time() - cur["start"])
                session = {"elapsed": elapsed, "cost": round(elapsed / 3600 * (cur["price"] or price or 0), 3)}
        except OSError:
            pass   # запись истории не должна ломать главный экран
        d = jobs.state()
        task = d and {"kind": d["kind"], "active": d["active"], "unseen": d["done"] and not d.get("seen")}
        notify.low_balance(credit, price, lang)
        return {"balance": credit, "low_balance": credit < LOW_BALANCE, "session": session, "task": task,
                "hours_left": credit / price if price else None, "status": st,
                "game_names": self._game_names(st["archives"]), "busy": self._busy()}

    def _game_names(self, archive_names):
        missing = [n for n in archive_names if n.startswith("pfx--") and n[5:] not in self._names]
        if missing:
            self._names.update(steamnames.for_archives(missing))
        return self._names

    def _busy(self):
        """Идёт ли операция: короткая в окне или play/stop в фоновом процессе."""
        return self._local_busy() or bool((jobs.state() or {}).get("active"))

    def _local_busy(self):
        return bool(self._job and not self._job.done)

    # ---- «Играть»: выбор машины
    def _disk(self, disk, tpl):
        return jobs.disk_size(disk, tpl)

    def offers(self, disk=None):
        """Подходящие машины (лучшие по полной цене часа) под диск disk ГБ и баланс."""
        try:
            core.require_no_machine()
            tpl = core.template()
            disk = self._disk(disk, tpl)
            rows = market.search(disk)
            credit = core.balance()
        except VastgameError as e:
            return {"error": error_data(e)}
        try:
            last = market.last_machine(disk)
        except VastgameError:
            last = None          # не удалось проверить последнюю машину — список машин всё равно показать
        row = lambda o: {"id": o["id"], "gpu": o["gpu_name"], "country": market.country(o),
                         "price": o["dph_total"], "traffic": o["_traffic"], "total": o["_total"],
                         "reliability": o["reliability"], "failed": o.get("_failed")}
        return {"disk": disk, "disk_min": DISK_MIN, "disk_max": DISK_MAX, "min_rel": market.MIN_REL, "count": len(rows), "balance": credit,
                "sync": self._sync(), "offers": [row(o) for o in rows[:OFFERS_SHOWN]],
                "last": last and {"gpu": last["gpu"], "country": last["country"], "start": last["start"],
                                  "offer": last["offer"] and row(last["offer"])}}

    def check_offer(self, offer_id, disk=None):
        """Свежие данные машины прямо перед подтверждением аренды."""
        try:
            disk = self._disk(disk, core.template())
            o = core.offer_by_id(int(offer_id), disk)
            credit = core.balance()
        except VastgameError as e:
            return {"error": error_data(e)}
        return {"id": o["id"], "gpu": o["gpu_name"], "country": market.country(o), "price": o["dph_total"],
                "disk": disk, "balance": credit, "hours": credit / o["dph_total"] if o["dph_total"] else None,
                "bad_reason": core.bad_host_reason(o)}

    def _sync(self):
        """Тумблеры синхронизации: последний выбор человека, иначе значения по умолчанию."""
        saved = settings.get("sync") or {}
        return {k: bool(saved.get(k, v)) for k, v in core.SYNC_DEFAULTS.items()}

    def play(self, offer_id, sync=None, disk=None, lang=None):
        """Арендовать машину (человек уже ответил «да») — в фоновом процессе, который переживёт
        закрытие окна. sync — тумблеры синхронизации (запоминаются), disk — ГБ с ползунка,
        lang — язык окна для уведомления в конце."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        sync = {k: bool((sync or {}).get(k, v)) for k, v in self._sync().items()}
        settings.set("sync", sync)
        if not jobs.spawn("play", offer_id=int(offer_id), sync=sync, disk=disk, lang=lang):
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        return {"ok": True}

    def stop(self, lang=None):
        """«Закончить»: выгрузить всё, проверить и удалить машину — в фоновом процессе."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        if not jobs.spawn("stop", lang=lang):
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
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

    def session_progress(self, since=0):
        """Ход «Играть»/«Закончить» из фонового процесса (job.json) начиная с события номер since."""
        d = jobs.state()
        if not d:
            return None
        since = int(since)
        return {"kind": d["kind"], "elapsed": int((d.get("ended") or time.time()) - d["started"]),
                "stage": d["stage"], "events": d["events"][since:], "next": len(d["events"]),
                "archives": d["archives"], "question": None, "result": d["result"], "error": d["error"],
                "done": d["done"], "game_names": self._game_names(d["archives"])}

    def cancel_start(self):
        """«Отменить запуск и удалить машину» (человек подтвердил на странице). Удаляем сразу, если
        машина уже арендована; фоновый процесс увидит флаг и закончит (и удалит сам, если аренда
        ещё шла). Процесс завис или умер — машина всё равно удалена отсюда."""
        iid = jobs.cancel_play()
        if iid and core._still_there(iid) is not False:
            ok, msg = core.delete_instance(iid)
            if not ok:
                return {"error": {"key": "core.delete_failed", "params": {"msg": msg or ""}, "text": msg or ""}}
        return {"ok": True, "iid": iid}

    def session_seen(self):
        """Человек посмотрел результат запуска/завершения — больше не показывать его при открытии окна."""
        jobs.mark_seen()

    # ---- Настройка
    def setup_status(self):
        """Состояние первой настройки: шаги (готово / нужно внимание / не настроено)."""
        try:
            return setupcheck.status()
        except VastgameError as e:
            return {"error": error_data(e)}

    def _simple(self, fn, *args):
        """Вызвать функцию мастера; ошибку — странице с ключом перевода."""
        try:
            return {"ok": True, "result": fn(*args)}
        except VastgameError as e:
            return {"error": error_data(e)}

    def _background(self, kind, fn, with_job=False):
        """Долгое действие настройки (установка, вход в Google) — в фоне; ход — через progress().
        with_job — передать fn сам Job, чтобы она сообщала этапы (job.say), а страница их показывала."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        job = self._job = Job(kind)

        def run():
            try:
                job.result = (fn(job) if with_job else fn()) or True
            except VastgameError as e:
                job.error = error_data(e)
            except Exception as e:
                job.error = {"key": None, "params": {}, "text": f"{type(e).__name__}: {e}"}
            finally:
                job.done = True

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def install_tool(self, tool):
        """Установить программу (страница уже спросила). Tailscale и Moonlight — официальные файлы
        с проверкой подписи (installer.py, Homebrew не нужен); rclone — через Homebrew (в собранном
        приложении он встроен, установка нужна только при запуске из папки проекта)."""
        if tool == "tailscale_app":
            return self._background("tailscale", self._install_tailscale, with_job=True)
        if tool == "moonlight":
            return self._background("install", installer.moonlight)
        brew = paths.find("brew")
        if tool not in BREW:
            return {"error": {"key": None, "params": {}, "text": "unknown tool"}}
        if not brew:
            return {"error": {"key": "setup.err.no_brew", "params": {}, "text": "no brew"}}

        def run():
            r = subprocess.run([brew, "install", *BREW[tool]], capture_output=True, text=True, timeout=1800)
            if r.returncode != 0:
                raise VastgameError("brew install failed", key="setup.err.install_failed", code=r.returncode,
                                    out=(r.stderr or r.stdout).strip()[-300:])
        return self._background("install", run)

    def _open_ts_login(self, url):
        self._ts_url = url
        webbrowser.open(url)

    def tailscale_reopen_login(self):
        """«Открыть страницу входа ещё раз» — вкладка не загрузилась или её закрыли."""
        if not self._ts_url:
            return {"error": {"key": "setup.err.ts_no_link", "params": {}, "text": "no link"}}
        webbrowser.open(self._ts_url)
        return {"ok": True}

    def _install_tailscale(self, job):
        """Установить Tailscale и довести до входа без кнопок в его окне: открыть Tailscale (macOS
        покажет «разрешить расширение» и «разрешить VPN»), дождаться, пока он заработает, и открыть
        страницу входа в браузере (кнопка входа в окне Tailscale бывает мёртвой)."""
        job.say("Скачиваю и устанавливаю Tailscale", key="setup.ts_install.installing")
        installer.tailscale()
        job.say("Tailscale установлен — разреши его в окнах macOS", key="setup.ts_install.approve")
        subprocess.Popen(["open", "-a", "Tailscale"])
        state = setup.tailscale_wait_ready()
        if state is None:
            raise VastgameError("Tailscale не заработал за 10 минут", key="setup.err.ts_not_ready")
        if state in ("NeedsLogin", "NeedsMachineAuth"):
            job.say("Открыл страницу входа Tailscale", key="setup.ts_login.waiting")
            setup.tailscale_login(self._open_ts_login)
        return True

    def tailscale_login(self):
        """«Войти в Tailscale»: ссылка входа — в браузер, ждём входа в фоне (ход — через progress())."""
        def run(job):
            job.say("Открыл страницу входа Tailscale", key="setup.ts_login.waiting")
            return setup.tailscale_login(self._open_ts_login)
        return self._background("tailscale", run, with_job=True)

    def open_tailscale(self):
        """Открыть приложение Tailscale, чтобы войти в сеть."""
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Tailscale"])

    def save_vast_key(self, key):
        r = self._simple(setup.save_vast_key, key)
        if r.get("ok"):
            try:
                sshkey.ensure()          # сразу и SSH-ключ в аккаунт — без него Vast не запустит машину
            except VastgameError:
                pass                     # шаг «SSH-ключ» покажет, что не так; перед арендой — ещё попытка
        return r

    def add_ssh_key(self):
        return self._simple(sshkey.ensure)

    def move_vast_key(self):
        """Перенести ключ из старого файла ~/.vast_api_key в Keychain и удалить файл (страница спросила)."""
        def move():
            credit = setup.save_vast_key(KEY_FILE.read_text())
            KEY_FILE.unlink()
            return credit
        return self._simple(move)

    def save_google_client(self, cid, secret):
        return self._simple(setup.save_google_client, cid, secret)

    def google_login(self):
        """Вход в Google (браузер) и отправка доступа в аккаунт Vast — в фоне."""
        return self._background("google", lambda: (setup.google_login(), setup.send_google_to_vast()) and True)

    def autostart_install(self, name="resume"):
        """Включить службу автозапуска (человек нажал «Включить»): "resume" — продолжать после
        перезагрузки и следить за балансом, "app" — открывать окно при входе в систему."""
        if name not in autostart.NAMES:
            return {"error": {"key": None, "params": {}, "text": "unknown service"}}
        if not autostart.install(name):
            return {"error": {"key": "setup.err.autostart", "params": {}, "text": "launchctl bootstrap failed"}}
        return {"ok": True}

    def autostart_uninstall(self, name="resume"):
        if name not in autostart.NAMES:
            return {"error": {"key": None, "params": {}, "text": "unknown service"}}
        autostart.uninstall(name)
        return {"ok": True}

    def reset_local(self):
        """«Сбросить vastgame на этом Mac» (страница дважды спросила). Нельзя, пока идёт запуск или
        завершение игры — фоновой операции нужны и ключи, и её файл хода."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        return {"ok": True, "deleted": setup.reset_local()}

    def send_google(self):
        return self._simple(setup.send_google_to_vast)

    def save_tailscale_key(self, key):
        return self._simple(setup.save_tailscale_key, key)

    def save_template(self, disk, lang, res):
        """Создать или изменить шаблон; SYNC_STEAM_GAMES в шаблоне не трогаем (его задают тумблеры)."""
        def save():
            tpl = my_template()
            games = setup.env_value((tpl or {}).get("env"), "SYNC_STEAM_GAMES") or "0"
            ok, msg = setup.save_template(int(disk), (lang or "").strip(), (res or "").strip() or "1920x1200", games)
            if not ok:
                raise VastgameError(msg, key="setup.err.template")
        if not str(disk).isdigit() or not 30 <= int(disk) <= 2000:
            return {"error": {"key": "setup.err.disk", "params": {}, "text": "disk"}}
        if lang and not re.fullmatch(r"[a-z_]+", lang.strip()):
            return {"error": {"key": "setup.err.lang", "params": {}, "text": "lang"}}
        if res and not re.fullmatch(r"\d{3,4}x\d{3,4}", res.strip()):
            return {"error": {"key": "setup.err.res", "params": {}, "text": "res"}}
        return self._simple(save)

    def refresh_template(self):
        def refresh():
            ok, msg = setup.refresh_template()
            if not ok:
                raise VastgameError(msg, key="setup.err.template")
        return self._simple(refresh)

    # ---- История и траты
    def history(self, days=30, all_machines=False):
        """Сессии и траты за days дней: реальные списания Vast + своя запись сессий.
        all_machines — включить и неигровые машины аккаунта."""
        try:
            return history.summary(int(days), bool(all_machines))
        except VastgameError as e:
            return {"error": error_data(e)}

    # ---- Игры в облаке
    def games(self):
        """Игры и сохранения в облаке (прямо с Google Drive, машина не нужна)."""
        try:
            data = cloudgames.listing()
        except VastgameError as e:
            return {"error": error_data(e)}
        for g in data["games"]:
            g.pop("files")                          # странице не нужны
        return data

    def games_remove(self, name):
        """Убрать архив из облака (страница уже спросила). Фоновая операция: при работающей машине
        идёт через wolf по Tailscale SSH, и может понадобиться подтвердить вход по ссылке."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        job = self._job = Job("games")

        def run():
            try:
                job.result = {"how": cloudgames.remove(str(name), on_notice=job.tailscale)}
            except VastgameError as e:
                job.error = error_data(e)
            except Exception as e:
                job.error = {"key": None, "params": {}, "text": f"{type(e).__name__}: {e}"}
            finally:
                job.done = True

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

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
        if not moonlight.path():
            return {"error": {"key": "moonlight.missing", "params": {}, "text": "Moonlight не установлен"}}
        settings.set("moonlight_app", app)
        # Второй запуск, пока идёт первый (связка — до минуты), открыл бы вторую трансляцию:
        # Sunshine отвечает ей «приложение уже запущено», и обе обрываются (живой тест 2026-09-25)
        if not self._moonlight_lock.acquire(blocking=False):
            return {"ok": True, "busy": True}
        try:
            return self._open_moonlight(host, app)
        finally:
            self._moonlight_lock.release()

    def _open_moonlight(self, host, app):
        if moonlight.apps(host) is None:
            # Moonlight ещё не связан с этой машиной — связать самим, без ручного PIN
            node = wolfnode.ts_node()
            if not node:
                return {"error": {"key": "pair.no_machine", "params": {}, "text": "машины нет в Tailscale"}}
            pairing.pair(host, node[1])
        moonlight.stream(host, app)
        return {"ok": True}

    def sunshine_login(self):
        """Логин кабинета Sunshine, который задало приложение, — показать человеку по его просьбе."""
        pw = pairing.keyring.get_password(pairing.SERVICE, "SUNSHINE_PASSWORD")
        return {"user": pairing.USER, "password": pw} if pw else {"user": None}

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
        """Закрытие окна. «Играть»/«Закончить» идут в фоновом процессе — окно закрывается спокойно.
        Идёт короткая операция в самом окне (убрать игру, установка) — спросить на странице.
        Спрашиваем страницей, а не системным диалогом: этот обработчик работает в главном потоке,
        и системный диалог из него повесил бы окно."""
        if self._local_busy() and not self._force_close:
            threading.Thread(target=lambda: self._window.evaluate_js("askClose()"), daemon=True).start()
            return False   # не закрывать
        return True


def _safe(fn):
    """Обёртка для функций страницы: что бы ни случилось, страница получает ответ. Без неё
    неожиданная ошибка в Python оставляет страницу ждать вечно («Проверяю настройку…»)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except VastgameError as e:
            return {"error": error_data(e)}
        except Exception as e:                  # noqa: BLE001 — показать любую причину
            _log_error(fn.__name__)
            return {"error": jobs.unexpected(e)}
    return wrapper


def _log_error(where):
    """Записать неожиданную ошибку с подробностями в app.log (папка настроек) — для разбора."""
    try:
        log = settings.config_dir() / "app.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            log.unlink()
        with open(log, "a") as f:
            f.write(f"--- {time.strftime('%F %T')} {where}\n{traceback.format_exc()}\n")
    except OSError:
        pass


for _name, _fn in list(vars(Api).items()):
    if callable(_fn) and not _name.startswith("_"):
        setattr(Api, _name, _safe(_fn))


def main():
    autostart.refresh()      # служба стоит, но проект переехал — поправить пути (выключенную не трогаем)
    api = Api()
    window = webview.create_window("vastgame", str(HERE / "ui" / "index.html"), js_api=api,
                                   width=620, height=760, min_size=(520, 600))
    api._window = window
    window.events.closing += api._on_closing
    webview.start(debug=bool(os.environ.get("VASTGAME_DEBUG")))


if __name__ == "__main__":
    main()

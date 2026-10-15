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

import asks
import autostart
import cloudgames
import drive
import filelock
import geo
import core
import history
import installer
import latency
import jobs
import keystore
import market
import moonlight
import pairing
import parallel
import wolfnode
import notify
import settings
import paths
import setup
import setupcheck
import snapshot
import sshkey
from vastapi import KEY_FILE
import steamlib
import steamnames
import updater
from jobs import Job, error_data
from vastapi import TEMPLATE_TTL, VastgameError, my_template

HERE = Path(__file__).resolve().parent
LOW_BALANCE = notify.LOW_BALANCE   # $: ниже — предупреждение «баланс заканчивается»
OFFERS_PAGE = 15    # машин на странице выбора (показываются все подходящие, по страницам)
# Фильтры списка машин по умолчанию: все страны, любая скорость и пинг, сначала дешёвые
OFFER_FILTERS = {"countries": [], "gpus": [], "speed": 0, "ping": 0, "traffic": 1, "rel": 97, "sort": "price", "dir": "asc"}
# [0.3.2] Надёжность хоста, % — не ниже (фильтр на странице; по умолчанию 97, как было). Ниже REL_FLOOR не показываем:
# такие машины слишком часто пропадают (у Vast «надёжность» — доля времени, когда хост был на связи)
REL_LEVELS = (97, 95, 90)
REL_FLOOR = 0.90
TRAFFIC_LIMITS = (0, 0.1, 0.5, 1, 2, 5)   # $ за ТБ, не дороже (0 — любой, 0.1 — бесплатный); только вкладка Docker
OFFER_SORTS = ("price", "ping", "net", "traffic", "gpu")   # колонки, по которым сортирует страница
DISK_MIN, DISK_MAX = jobs.DISK_MIN, jobs.DISK_MAX   # пределы ползунка диска, ГБ
SPARE = 10          # машин из списка человека на замену, если Vast не запустит выбранную
PROG_ID = re.compile(r"^(game|prog|pfx):[^\x00-\x1f]{1,400}$")   # [v5.4] программа игры (путь проверяет машина)
PROG_ARGS = re.compile(r"^[^\x00-\x1f]{0,300}$")                  # [v5.6] параметры программы-загрузчика
SHORTCUT_APPID = 2147483648   # номера ярлыков Steam (игры не из магазина) — от него; обложек у них нет
REPLACE_WAIT = 180   # с: «Сменить машину» — сколько ждать, пока прежний запуск закончится и Vast уберёт машину
REPLACE_POLL = 2
STEAM_LOGIN_MIN = 20   # мин: окно входа Steam ждёт человека
ASK_POLL = 10       # с: как часто окно спрашивает машину, нет ли новых вопросов (новая / удалённая игра)
# Установка программ через Homebrew (Mac). --cask — приложение, иначе — программа командной строки
BREW = {"rclone": ["rclone"], "tailscale_app": ["--cask", "tailscale-app"], "moonlight": ["--cask", "moonlight"]}



def _loading_page(text):
    """Страница-заглушка окна входа Steam, пока грузится сам Steam: текст по центру, светлая и тёмная тема."""
    import html
    return ("<!doctype html><meta charset=utf-8><style>:root{color-scheme:light dark}"
            "body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
            "font:16px -apple-system,'Segoe UI',sans-serif;color:#666;text-align:center;padding:0 40px}"
            "</style><body><p>" + html.escape(text) + "</p>")

class Api:
    """Функции для страницы. Возвращают словари — pywebview передаёт их в JavaScript как объекты.
    Атрибуты с «_» pywebview странице не показывает."""

    def __init__(self):
        self._job = None
        self._names = {}          # {appid: название игры} — для сохранений pfx--<appid>
        self._window = None
        self._force_close = False
        self._tray = None            # Windows: значок в трее (tray.py) — закрытие окна его прячет
        self._quitting = False       # «Выйти» из трея / подтверждённое закрытие — закрыть по-настоящему
        self._ts_url = None       # ссылка входа Tailscale — только в памяти, чтобы открыть её ещё раз
        self._moonlight_lock = threading.Lock()   # «Запустить Moonlight» — не больше одного сразу
        self._opened = set()      # ссылки подтверждения Tailscale SSH, уже открытые в браузере
        self._display = (0, None) # (когда узнали, родной экран) — system_profiler небыстрый
        self._updates = updater.Checker()   # есть ли версия новее на GitHub — проверяет в фоне
        self._update_ready = None           # {version, app} — новая версия скачана и проверена, можно ставить
        self._card = None                   # карточка вопроса машины поверх всех окон (или None)
        self._card_only = False             # процесс только карточки (окно закрыто) — закрыть карточку = выйти
        self._front = None                  # приложение, которое было впереди до карточки (Moonlight) — вернуть
        self._answer_lock = threading.Lock()  # ответы машине — по одному (второй сразу мог бы потеряться)
        self._steam_lock = threading.Lock()   # окно входа Steam — одно

    # ---- настройки
    def settings(self):
        """Сохранённые настройки окна (язык — только если его выбрали сами), номер версии приложения и, если окно
        открылось в конце обновления, что сказать («обновлено» / «не встало»). Страница спрашивает это первым —
        тут же новая версия ставит сторожу знак «я открылась» (updater.after_start)."""
        return {"language": settings.get("language"), "version": updater.VERSION, "update": updater.after_start(),
                "platform": sys.platform, "sounds": settings.get("ui_sounds", True) is not False}

    def hide_cursor(self):
        """[2026-10-15] Управляют геймпадом — спрятать указатель мыши, пока мышь или тачпад не сдвинут (macOS сама
        покажет его при движении). На других системах — ничего (страница прячет курсор стилем)."""
        if sys.platform != "darwin":
            return {"ok": False}
        try:
            import AppKit
            from PyObjCTools import AppHelper
            AppHelper.callAfter(lambda: AppKit.NSCursor.setHiddenUntilMouseMoves_(True))   # AppKit — из главного потока
            return {"ok": True}
        except Exception:     # noqa: BLE001 — курсор не главное, окно работает и без этого
            return {"ok": False}

    def set_sounds(self, on):
        """[2026-10-15] Звуки окна (наведение, нажатие, запуск трансляции) — вкл/выкл."""
        settings.set("ui_sounds", bool(on))
        return {"ok": True}

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
            st, credit = parallel.run(core.status, core.balance)
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
                "hours_left": credit / price if price else None, "status": {**st, "progs": self._progs(st.get("progs"))},
                "game_names": self._game_names(st["archives"]), "busy": self._busy(),
                "play_now": settings.get("play_now") if st["machine"] or task else None}

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

    def _docker(self, docker):
        """Вкладка списка машин: выбор человека (запоминается), иначе прошлый, иначе Docker — основной режим с 0.3.1
        (машин в нём в разы больше, чем в KVM)."""
        if docker is None:
            return bool(settings.get("offers_docker", True))
        settings.set("offers_docker", bool(docker))
        return bool(docker)

    def _launching(self):
        """Идёт запуск («Играть» в фоне, машина ещё не готова) — предложение машины, которая запускается ({} — ещё не
        арендована); иначе None."""
        d = jobs.state()
        if not d or d["kind"] != "play" or not d.get("active") or d.get("done"):
            return None
        m = d.get("meta") or {}
        return m.get("offer") or next((r["offer"] for r in m.get("race") or [] if r.get("offer")), None) or {}

    def offers(self, disk=None, docker=None, replace=False):
        """Подходящие машины (лучшие по полной цене часа) под диск disk ГБ и баланс.
        docker — вкладка «Docker» (основная с 0.3.1) (None — как в прошлый раз).
        replace — каталог во время запуска («Сменить машину»): машина уже есть — это та, что запускается;
        в ответе current — она (для сравнения цены и пинга), replace — идёт ли ещё запуск."""
        launching = self._launching() if replace else None
        def last_machine():
            try:
                return market.last_machine(disk, docker)
            except VastgameError:
                return None      # не удалось проверить последнюю машину — список машин всё равно показать

        try:
            *_, tpl, credit = parallel.run(*([core.require_no_machine] if launching is None else []),
                                           lambda: core.template(TEMPLATE_TTL), core.balance)
            disk = self._disk(disk, tpl)
            docker = self._docker(docker)
            rows, last = parallel.run(lambda: market.search(disk, min_rel=REL_FLOOR, docker=docker),  # порог надёжности —
                                      last_machine)                                            # фильтром на странице
        except VastgameError as e:
            return {"error": error_data(e)}
        remembered, me = latency.known(), geo.home()

        def row(o):
            geo_ = o.get("geolocation") or ""
            return {"id": o["id"], "gpu": o["gpu_name"], "country": market.country(o),
                    "cc": geo_.rsplit(",", 1)[-1].strip().upper() or "?",
                    "price": o["dph_total"], "traffic": o["_traffic"], "total": o["_total"],
                    "reliability": o["reliability"], "failed": o.get("_failed"),
                    "failed_why": o.get("_failed_why"), "start_min": o.get("_start_min"),
                    "net_down": round(o.get("inet_down") or 0), "net_up": round(o.get("inet_up") or 0),
                    # цена трафика, $ за ТБ (дороже из двух сторон) — фильтр «Трафик» во вкладке Docker
                    "traffic_tb": round(max(o.get("inet_up_cost") or 0, o.get("inet_down_cost") or 0) * 1000, 2),
                    # $ за ТБ отдельно: ↓ на машину (игры и сохранения из облака), ↑ с машины (трансляция, выгрузка)
                    **_traffic(o), "gpus": o.get("_gpus", 1),
                    "ping": latency.for_offer(o, remembered, me)}
        current = None
        if launching is not None:
            o = launching
            current = o and {"gpu": o.get("gpu_name") or "?", "country": market.country(o), "price": o.get("dph_total"),
                             "ping": latency.for_offer(o, remembered, me), "machine_id": o.get("machine_id"), **_traffic(o)}
        out = {"replace": launching is not None, "current": current,
                "docker": docker, "disk": disk, "disk_min": DISK_MIN, "disk_max": DISK_MAX, "min_rel": market.MIN_REL, "count": len(rows), "balance": credit,
                "sync": self._sync(), "offers": [row(o) for o in rows], "page_size": OFFERS_PAGE,
                "filters": self._offer_filters(),
                "last": last and {"gpu": last["gpu"], "country": last["country"], "start": last["start"],
                                  "offer": last["offer"] and row(last["offer"])}}
        if launching is None:                     # [0.4.5] обычный список — показать сразу в следующий раз
            snapshot.save("offers:" + ("docker" if docker else "kvm"), out)
        return out

    def check_offer(self, offer_id, disk=None, docker=False):
        """Свежие данные машины прямо перед подтверждением аренды."""
        try:
            disk = self._disk(disk, core.template(TEMPLATE_TTL))
            o, credit = parallel.run(lambda: core.offer_by_id(int(offer_id), disk, bool(docker)), core.balance)
        except VastgameError as e:
            return {"error": error_data(e)}
        # [2026-10-15] образец для замены, если выбранную займут, пока она арендуется (_start_play → jobs, hint)
        self._checked = {"id": int(o["id"]), "price": o.get("dph_total") or 0, "traffic": jobs._traffic(o),
                         "down": o.get("inet_down") or 0}
        return {"id": o["id"], "gpu": o["gpu_name"], "country": market.country(o), "price": o["dph_total"],
                "disk": disk, "balance": credit, "hours": credit / o["dph_total"] if o["dph_total"] else None,
                "bad_reason": core.bad_host_reason(o), **_traffic(o)}

    def _offer_filters(self):
        saved = settings.get("offer_filters") or {}
        return {k: saved.get(k, v) for k, v in OFFER_FILTERS.items()}

    def save_offer_filters(self, f):
        """Запомнить фильтры списка машин (страны, скорость от, пинг до, сортировка: колонка и направление)."""
        if not isinstance(f, dict):
            return {"error": {"key": None, "params": {}, "text": "bad arguments"}}
        clean = {"countries": [c for c in f.get("countries") or [] if isinstance(c, str)][:100],
                 "gpus": [g for g in f.get("gpus") or [] if isinstance(g, str)][:100],
                 "speed": max(0, int(f.get("speed") or 0)), "ping": max(0, int(f.get("ping") or 0)),
                 "traffic": f.get("traffic") if f.get("traffic") in TRAFFIC_LIMITS else OFFER_FILTERS["traffic"],
                 "rel": f.get("rel") if f.get("rel") in REL_LEVELS else OFFER_FILTERS["rel"],
                 "sort": f.get("sort") if f.get("sort") in OFFER_SORTS else "price",
                 "dir": "desc" if f.get("dir") == "desc" else "asc"}
        settings.set("offer_filters", clean)
        return {"ok": True}

    def _sync(self):
        """Тумблеры синхронизации: последний выбор человека, иначе значения по умолчанию."""
        saved = settings.get("sync") or {}
        return {k: bool(saved.get(k, v)) for k, v in core.SYNC_DEFAULTS.items()}

    # ---- [0.4.5] Прошлый ответ медленного экрана — показать сразу, пока идёт свежий (snapshot.py)
    def snapshot(self, name):
        """Последний удачный ответ экрана name или None. То, что человек мог поменять с тех пор (фильтры, выбор
        игр, тумблеры), — нынешнее, а не из прошлого ответа. "offers:auto" — вкладка, открытая в прошлый раз."""
        if name == "offers:auto":
            name = "offers:" + ("docker" if self._docker(None) else "kvm")
        d = snapshot.get(name)
        if not isinstance(d, dict):
            return None
        if name.startswith("offers:"):
            d.update(filters=self._offer_filters(), sync=self._sync())
        return d

    def play(self, offer_id, sync=None, disk=None, lang=None, spare=None, docker=False, select=None, game=None):
        """Арендовать машину (человек уже ответил «да») — в фоновом процессе, который переживёт
        закрытие окна. sync — тумблеры синхронизации (запоминаются), disk — ГБ с ползунка,
        lang — язык окна для уведомления в конце, spare — следующие машины его списка (на замену,
        если Vast не запустит выбранную), docker — вкладка «Docker» (основная с 0.3.1).
        select — не используется с v5.7 (выбора игр нет: свои игры из облака — все, сохранения — всегда); параметр
        оставлен, чтобы страница прежней версии не ломалась.
        game — игра из библиотеки ({"appid", "account"}): машина (v4.8) входит в Steam этим аккаунтом, Steam сам
        качает и запускает игру. Без неё — просто машина с рабочим столом, как раньше."""
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        return self._start_play(offer_id, sync, disk, lang, spare, docker, select, game)

    def _start_play(self, offer_id, sync=None, disk=None, lang=None, spare=None, docker=False, select=None, game=None):
        """Сама аренда в фоне (play и replace). Идёт другая операция с машиной — busy."""
        play = self._library_game(game) if game else None
        if game and not play:
            return {"error": {"key": "library.bad_game", "params": {}, "text": "game is not in the library"}}
        sync = {k: bool((sync or {}).get(k, v)) for k, v in self._sync().items()}
        # [v5.7] Выбора игр и сохранений больше нет (решение Алексея 2026-10-09: человеку меньше решений): игры Steam
        # качает Steam, свои игры из облака — все ("own": "all"; новая на диске — по вопросу машины), сохранения — всегда.
        # select со страницы прежних версий не учитывается; выбор игр из прошлых аренд (sync_sel) — для старых скриптов
        chosen = cloudgames.remember({})
        chosen["games"] = [a for a in chosen["games"] if a.startswith("game--")]
        chosen["saves_off"] = []
        chosen["own"] = "all"
        chosen["mods"] = cloudgames.mods_answers()                                     # [v5.0] «сохранять моды?»
        chosen["progs"] = settings.get("progs_sel") or {}                              # [v5.4] «вместе с игрой»
        chosen["launch"] = settings.get("launch_sel") or {}                            # [v5.6] «запускать через»
        chosen["gdata"] = cloudgames.gdata_list()                                      # [v5.6] «игру в облаке»
        chosen["recipes"] = cloudgames.recipes_list()                                  # [v5.6] перевод FF14…
        if play and not play.get("mods"):                  # [v5.7] «Без модов» — и без рецепта (перевод — тоже мод)
            chosen["recipes"].pop(play["appid"], None)
        sync.update(steam_games=False, other_games=True, saves=True)
        settings.set("sync", sync)
        sync["select"] = chosen
        settings.set("play_now", play)       # что запускаем — экрану хода и главному (окно могут закрыть и открыть)
        if play:
            sync["play"] = play
            settings.applog(f"play: {play['appid']} аккаунтом …{play['account'][-4:]}")
        spare = [int(x) for x in (spare or []) if str(x).isdigit() and int(x) != int(offer_id)][:SPARE]
        hint = getattr(self, "_checked", None)
        hint = {k: hint[k] for k in ("price", "traffic", "down")} if hint and hint.get("id") == int(offer_id) else None
        if not jobs.spawn("play", offer_id=int(offer_id), sync=sync, disk=disk, lang=lang, spare=spare,
                          docker=bool(docker), hint=hint):
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        return {"ok": True}

    def replace(self, offer_id, disk=None, lang=None, spare=None, docker=False, select=None, game=None):
        """«Заменить машину» из каталога во время запуска (человек подтвердил): отменить запуск, удалить машину, что
        запускается, дождаться, что фоновый процесс закончил и у Vast машин нет, — и только тогда арендовать
        выбранную. Двух машин не бывает ни на миг: аренда — тем же путём, что «Играть» (там ещё раз проверяется,
        что машин нет). Запуск уже закончился: машина готова — отказ (менять — через «Закончить», на ней уже может
        быть несохранённое); не удался и машины нет — обычная аренда."""
        if self._local_busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        if self._launching() is None:
            d = jobs.state() or {}
            if d.get("kind") == "play" and d.get("done") and not d.get("error"):
                return {"error": {"key": "replace.ready", "params": {}, "text": "machine is ready"}}
            return self.play(offer_id, None, disk, lang, spare, docker, select, game)
        job = self._job = Job("replace")

        def run():
            try:
                job.say("Отменяю запуск и удаляю машину", "info", key="replace.cancelling")
                for iid in jobs.cancel_play() or []:
                    ok, msg = core.cancel_delete(iid)
                    if not ok:
                        raise VastgameError(f"Не удалось удалить машину {iid}: {msg}", key="core.delete_failed",
                                            msg=msg or "")
                job.say("Жду, пока Vast уберёт машину", "info", key="replace.waiting")
                end = time.time() + REPLACE_WAIT
                while (jobs.state() or {}).get("active") or core.list_instances():
                    if time.time() > end:
                        raise VastgameError("Прежняя машина всё ещё видна у Vast — новую не арендую",
                                            key="replace.stuck")
                    time.sleep(REPLACE_POLL)
                jobs.mark_seen()                        # итог отменённого запуска показывать незачем
                job.say("Арендую выбранную машину", "info", key="replace.renting")
                r = self._start_play(offer_id, None, disk, lang, spare, docker, select, game)
                job.error, job.result = r.get("error"), None if r.get("error") else {"ok": True}
            except VastgameError as e:
                job.error = error_data(e)
            except Exception as e:                      # noqa: BLE001 — показать любую причину
                _log_error("replace")
                job.error = jobs.unexpected(e)
            finally:
                job.done = True

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def stop(self, lang=None):
        """«Закончить»: выгрузить всё, проверить и удалить машину — в фоновом процессе."""
        settings.applog(f"закончить: нажато, Moonlight {'идёт' if moonlight.streaming() else 'не идёт'}")
        if self._busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        if not jobs.spawn("stop", lang=lang):
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        return {"ok": True}

    def emergency_delete(self, machine_id):
        """Удалить машину без выгрузки. Страница уже дважды спросила; удаляем только машину,
        которую человек видел (machine_id), и только когда не идёт другая операция."""
        settings.applog(f"удалить без выгрузки: нажато, Moonlight {'идёт' if moonlight.streaming() else 'не идёт'}")
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
        iids = jobs.cancel_play() or []
        for iid in iids:                     # выбранная или все машины забега
            # Удаляем, не глядя в список: сразу после аренды Vast может ещё не показывать там машину
            ok, msg = core.cancel_delete(iid)
            if not ok:
                return {"error": {"key": "core.delete_failed", "params": {"msg": msg or ""}, "text": msg or ""}}
        return {"ok": True, "iid": iids[0] if len(iids) == 1 else None, "iids": iids}

    def session_seen(self):
        """Человек посмотрел результат запуска/завершения — больше не показывать его при открытии окна."""
        jobs.mark_seen()

    # ---- Настройка
    def setup_status(self):
        """Состояние первой настройки: шаги (готово / нужно внимание / не настроено)."""
        try:
            d = setupcheck.status()
        except VastgameError as e:
            return {"error": error_data(e)}
        snapshot.save("setup", d)
        return d

    # ---- Простая настройка: по одному шагу — программы → Vast → Google → Tailscale → остальное само
    def wizard_state(self):
        """Что уже готово (только проверки). Страница начинает с первого неготового шага."""
        from concurrent.futures import ThreadPoolExecutor
        mac = sys.platform == "darwin"
        programs = {"tailscale": bool(paths.tailscale_app() if mac else paths.find("tailscale")),
                    "moonlight": bool(moonlight.path())}
        # Проверки ходят в сеть (Vast, Google) — одновременно, чтобы экран «Проверяю…» не висел
        with ThreadPoolExecutor(3) as ex:
            v = ex.submit(setupcheck.vast_key)
            g = ex.submit(lambda: setupcheck.google(setupcheck._vast_secrets(), sizes=False))
            ts = ex.submit(setupcheck.tailscale_phase)
            v, g, ts = v.result(), g.result(), ts.result()
        vast = {"ok": v["state"] != "missing", "balance": v["params"].get("balance")}
        # Google — после Vast (токен уходит в аккаунт Vast). «Не отправлен в Vast» доделает последний шаг
        # сам; вход через старый общий клиент rclone («old») — войти заново
        google = {"ok": vast["ok"] and (g["state"] == "ok" or g["key"] == "setup.google.not_in_vast"),
                  "email": g["params"].get("email") if vast["ok"] else None}
        return {"platform": sys.platform, "programs": programs, "vast": vast, "google": google, "tailscale": ts}

    def tailscale_phase(self):
        """Этап подключения Tailscale — страница спрашивает раз в пару секунд, пока идёт этот шаг."""
        return setupcheck.tailscale_phase()

    def install_programs(self):
        """Поставить недостающие Tailscale и Moonlight (официальные файлы с проверкой подписи).
        Пароль Mac спросит сама macOS — один раз, для Tailscale; Moonlight ставится без пароля."""
        if sys.platform not in ("darwin", "win32"):
            return {"error": {"key": "wizard.install.not_mac", "params": {}, "text": "not mac"}}
        mac = sys.platform == "darwin"

        def run(job):
            if not moonlight.path():
                job.say("Устанавливаю Moonlight", key="wizard.install.moonlight")
                installer.moonlight()
            if not (paths.tailscale_app() if mac else paths.find("tailscale")):
                job.say("Устанавливаю Tailscale — macOS спросит пароль", key="wizard.install.tailscale")
                installer.tailscale()
            return True
        return self._background("install", run, with_job=True)

    def open_extension_settings(self):
        """Открыть раздел Системных настроек, где разрешают расширение Tailscale. macOS 15 и новее —
        «Объекты входа и расширения», раньше — «Конфиденциальность и безопасность»."""
        if sys.platform != "darwin":
            return
        import platform
        major = int((platform.mac_ver()[0] or "0").split(".")[0])
        url = ("x-apple.systempreferences:com.apple.LoginItems-Settings.extension" if major >= 15
               else "x-apple.systempreferences:com.apple.preference.security?General")
        subprocess.Popen(["open", url])

    def tailscale_up(self):
        """Включить Tailscale, если человек вошёл, но он выключен."""
        exe = paths.find("tailscale")
        try:
            r = paths.run([exe, "up"], 30, env=paths.tailscale_env()) if exe else None
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is None or r.returncode != 0:
            return {"error": {"key": "wizard.ts.up_failed", "params": {}, "text": "tailscale up failed"}}
        return {"ok": True}

    def wizard_finish(self):
        """Последний шаг простой настройки — всё, что можно сделать без человека."""
        return self._background("finish", self._finish_setup, with_job=True)

    def _finish_setup(self, job):
        job.say("Шаблон vastgame на Vast", key="wizard.finish.template")
        tpl = my_template()
        if not tpl:
            ok, msg = setup.save_template(100, "", "1920x1200", "0")     # как в расширенной настройке по умолчанию
        elif tpl.get("onstart") != setup.ONSTART:
            ok, msg = setup.refresh_template()                            # устарел — на свежий скрипт
        else:
            ok, msg = True, ""
        if not ok:
            raise VastgameError(msg, key="setup.err.template")
        job.say("SSH-ключ в Vast", key="wizard.finish.sshkey")
        sshkey.ensure()
        job.say("Доступ к Google Drive для машины", key="wizard.finish.google")
        if not {"RCLONE_REFRESH_TOKEN", "RCLONE_CLIENT_ID", "RCLONE_CLIENT_SECRET"} <= setupcheck._vast_secrets():
            setup.send_google_to_vast()
        job.say("Продолжать после перезагрузки", key="wizard.finish.autostart")
        if autostart.status("resume") not in ("on", "unsupported"):
            autostart.install("resume")      # не вышло — не беда: играть можно, шаг останется в расширенной
        return True

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
        self.open_tailscale()
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
        """Открыть приложение Tailscale, чтобы войти в сеть. Windows — tailscale-ipn.exe (значок возле часов)
        рядом с tailscale.exe; уже запущен — второй экземпляр просто выходит."""
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Tailscale"])
        elif sys.platform == "win32":
            exe = paths.find("tailscale")
            ipn = os.path.join(os.path.dirname(exe), "tailscale-ipn.exe") if exe else None
            if ipn and os.path.exists(ipn):
                subprocess.Popen([ipn], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **paths.detached())

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
        return {"ok": True, **setup.reset_local()}

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
            d = history.summary(int(days), bool(all_machines))
        except VastgameError as e:
            return {"error": error_data(e)}
        snapshot.save(f"history:{int(days)}:{'all' if all_machines else 'game'}", d)
        return d

    # ---- Игры в облаке
    def games(self):
        """Игры и сохранения в облаке (прямо с Google Drive, машина не нужна)."""
        try:
            data = cloudgames.listing()
        except VastgameError as e:
            return {"error": error_data(e)}
        for g in data["games"]:
            g.pop("files")                          # странице не нужны
        snapshot.save("games", data)
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

    def live(self):
        """Ход архивов и вопросы работающей машины прямо с её страницы статуса (без Vast — можно часто): главный
        экран спрашивает раз в 3 с, пока идёт запуск или синхронизация. {"archives": {имя: [состояние, текст, ход]},
        "asks": [...]}; машины нет в Tailscale — пусто."""
        node = wolfnode.ts_node()
        s = (wolfnode.wolf_status(node[1]) if node else None) or {}
        arch = {n: [v[1], v[2], getattr(v, "progress", None)] for n, v in s.items()
                if n not in ("boot", *core.NOT_ARCHIVES) and not core.ASK.match(n)}
        p = s.get(core.PLAY)
        play = p and {"state": p[1], "text": p[2], "progress": getattr(p, "progress", None)}
        return {"archives": arch, "asks": core.asks_of(s), "game_names": self._game_names(list(arch)),
                "play": play, "play_game": settings.get("play_now") if play else None,
                "streaming": moonlight.streaming() if play else None, "progs": self._progs(core.progs_of(s))}

    # ---- [v5.4] Программы игр: трейнеры и программы для модов — рядом с игрой, в её Proton (wolf-setup.sh v5.4)
    def _progs(self, progs):
        """Программы игр с машины + названия игр (из библиотеки или игры, с которой арендована машина)."""
        if not progs:
            return {}
        self._from_machine(progs)
        games = {g["appid"]: g.get("name") for g in (steamlib.cached() or {}).get("games") or []}
        now = settings.get("play_now") or {}
        for a, v in progs.items():
            v["name"] = games.get(a) or (now.get("name") if str(now.get("appid")) == a else "") or v.get("name") or ""
        return progs

    @staticmethod
    def _from_machine(progs):
        """[v5.7] Выбор, сделанный на машине помощником двойного щелчка («Вместе с игрой», «Запуск игры через»), — себе в
        память, один раз на каждое изменение (поле helper.t в отчёте машины): следующая машина получит его так же, как
        выбор из окна. Помощник только включает — выключают в окне."""
        seen = dict(settings.get("progs_helper_seen") or {})
        for a, v in progs.items():
            h = v.get("helper") if isinstance(v, dict) else None
            if not (str(a).isdigit() and isinstance(h, dict) and isinstance(h.get("t"), int) and h["t"] > seen.get(a, 0)):
                continue
            auto = [x for x in h.get("auto") or [] if isinstance(x, str) and PROG_ID.match(x)]
            if auto:
                sel = settings.get("progs_sel") or {}
                settings.set("progs_sel", {**sel, a: (sel.get(a) or []) + [x for x in auto if x not in (sel.get(a) or [])]})
            via = h.get("launch")
            if isinstance(via, dict) and isinstance(via.get("id"), str) and PROG_ID.match(via["id"]) \
                    and PROG_ARGS.match(str(via.get("args") or "")):
                settings.set("launch_sel", {**(settings.get("launch_sel") or {}), a: {"id": via["id"], "args": str(via.get("args") or "")}})
            seen[a] = h["t"]
            settings.applog(f"выбор с машины для {a}: вместе с игрой {auto or '—'}, запуск через {(via or {}).get('id', '—')}")
            settings.set("progs_helper_seen", seen)

    def prog_run(self, appid, ident):
        """Запустить программу рядом с игрой. {"result": ok|running|nogame|noproton|nofile}."""
        appid, ident = str(appid), str(ident)
        if not (appid.isdigit() and PROG_ID.match(ident)):
            return {"error": {"key": "progs.bad", "params": {}, "text": "bad program"}}
        node = wolfnode.ts_node()
        if not node:
            return {"error": {"key": "ask.no_machine", "params": {}, "text": "no machine"}}
        code, out = wolfnode.run(node[1], "prog-run", [appid, ident], timeout=60, on_notice=self._ssh_notice)
        m = re.search(r"prog: (\w+)", out or "")
        settings.applog(f"программа {ident} для {appid}: {m.group(1) if m else (out or '').strip()[-120:]}")
        return {"result": m.group(1) if m else "failed"}

    def prog_auto(self, appid, ident, on):
        """Тумблер «Вместе с игрой»: запомнить (уйдёт машинам при аренде) и сразу передать работающей машине."""
        appid, ident = str(appid), str(ident)
        if not (appid.isdigit() and PROG_ID.match(ident)):
            return {"error": {"key": "progs.bad", "params": {}, "text": "bad program"}}
        sel = settings.get("progs_sel") or {}
        cur = [x for x in sel.get(appid) or [] if x != ident] + ([ident] if on else [])
        sel = {**sel, appid: cur} if cur else {k: v for k, v in sel.items() if k != appid}
        settings.set("progs_sel", sel)
        node = wolfnode.ts_node()
        if node:
            try:
                wolfnode.run(node[1], "prog-auto", [appid, ident, "on" if on else "off"], timeout=30,
                             on_notice=self._ssh_notice)
            except VastgameError:
                pass                         # запомнено — уйдёт следующей машине; эта узнает при перезапуске игры
        return {"ok": True}

    def _prog_cmd(self, cmd, args, timeout):
        node = wolfnode.ts_node()
        if not node:
            return {"error": {"key": "ask.no_machine", "params": {}, "text": "no machine"}}
        try:
            code, out = wolfnode.run(node[1], cmd, args, timeout=timeout, on_notice=self._ssh_notice)
        except VastgameError as e:
            return {"error": error_data(e)}
        found = re.findall(r"prog: (\w+)", out or "")            # итог — последний ответ (перед ним бывает «ok» Steam)
        settings.applog(f"{cmd} {' '.join(args[:2])}: {found[-1] if found else (out or '').strip()[-120:]}")
        return {"result": found[-1] if found else "failed"}

    def prog_launch(self, appid, ident, args=""):
        """[v5.6] Запускать игру через программу ident («-» — как обычно), с параметрами: запомнить (уйдёт машинам при
        аренде) и передать работающей машине. {"result": ok|busy|nofile|failed} — busy: игра запущена, Steam не
        перезапустить — выбор сработает на следующей машине; нет машины — {"result": "saved"}."""
        appid, ident, args = str(appid), str(ident), str(args or "")
        if not (appid.isdigit() and (ident == "-" or PROG_ID.match(ident)) and PROG_ARGS.match(args)):
            return {"error": {"key": "progs.bad", "params": {}, "text": "bad program"}}
        sel = settings.get("launch_sel") or {}
        sel = {k: v for k, v in sel.items() if k != appid}
        if ident != "-":
            sel[appid] = {"id": ident, "args": args}
        settings.set("launch_sel", sel)
        if not wolfnode.ts_node():
            return {"result": "saved"}
        return self._prog_cmd("prog-launch", [appid, ident, args], 120)    # перезапуск Steam — до минуты с лишним

    def prog_install(self, appid, ident):
        """[v5.6] «Установить в игру»: один раз запустить установщик в префиксе игры (игра закрыта). Окно установщика —
        в Moonlight. {"result": installing|running|notinstalled|nofile|busy|failed}."""
        appid, ident = str(appid), str(ident)
        if not (appid.isdigit() and PROG_ID.match(ident)):
            return {"error": {"key": "progs.bad", "params": {}, "text": "bad program"}}
        return self._prog_cmd("prog-install", [appid, ident], 120)

    # ---- Библиотека Steam (steamlib.py): аккаунты и их игры — прямо из Steam, машина не нужна
    def library(self, fresh=False):
        """Библиотека: fresh=False — прошлый список (сразу), True — свежий из Steam (секунды). Без аккаунтов — пусто.
        [v5.0] mods — моды игр в облаке {номер: {name, bytes, mtime}} (свежие — с Drive); mods_save — тумблер «Моды».
        [v5.6] lgame — файлы игр их лаунчеров в облаке (так же), gdata — тумблер «Хранить игру в облаке»."""
        if not steamlib.accounts():
            return {"accounts": [], "games": [], "errors": []}
        # [0.4.5] список модов с Drive — одновременно с библиотекой из Steam
        lib, ext = parallel.run(lambda: (None if fresh else steamlib.cached()) or steamlib.library(),
                                lambda: cloudgames.extras() if fresh and drive.token() else None)
        mods = lgame = None
        if ext is not None:
            mods, lgame = ext["mods"], ext["lgame"]
            settings.set("mods_known", mods)
            settings.set("lgame_known", lgame)
        return {**lib, "progs_sel": settings.get("progs_sel") or {},                # [v5.4] программы «вместе с игрой»
                "launch_sel": settings.get("launch_sel") or {},              # [v5.6] «запускать игру через»
                "mods": mods if mods is not None else settings.get("mods_known") or {},
                "mods_save": cloudgames.mods_sel(),
                "lgame": lgame if lgame is not None else settings.get("lgame_known") or {},
                "gdata": settings.get("gdata_sel") or {},
                "recipes": settings.get("recipe_sel") or {},
                "recipe_apps": {a: r for r, ids in cloudgames.RECIPES.items() for a in ids}}

    def set_mods_save(self, appid, on):
        """[v5.1] Тумблер «Моды» у игры на карточке перед арендой — запомнить (уходит машине при аренде: накладывать и
        сохранять). Во время игры он заблокирован — машине на ходу не передаётся."""
        appid = str(appid)
        if not appid.isdigit():
            return {"error": {"key": "library.bad_game", "params": {}, "text": "bad appid"}}
        cloudgames.remember_mods(appid, bool(on))
        return {"ok": True}

    def set_gdata(self, appid, on):
        """[v5.6] Тумблер «Хранить игру в облаке» у игры на карточке перед арендой — запомнить (машине — при аренде)."""
        appid = str(appid)
        if not appid.isdigit():
            return {"error": {"key": "library.bad_game", "params": {}, "text": "bad appid"}}
        cloudgames.remember_gdata(appid, bool(on))
        return {"ok": True}

    def set_recipe(self, appid, on):
        """[v5.6] Переключатель рецепта у игры (у FF14 — «Русский перевод») — запомнить (машине — при аренде)."""
        appid = str(appid)
        if not cloudgames.recipe_for(appid):
            return {"error": {"key": "library.bad_game", "params": {}, "text": "no recipe"}}
        cloudgames.remember_recipe(appid, bool(on))
        return {"ok": True}

    def _library_game(self, game):
        """Игра из библиотеки, которую можно запустить этим аккаунтом: {"appid", "account", "name", "cap"} или None.
        Аккаунт — тот, у кого игра своя, или член семьи, которому её дали (Steam сам запишет настоящего владельца)."""
        if not isinstance(game, dict):
            return None
        appid, acc = str(game.get("appid") or ""), str(game.get("account") or "")
        lib = steamlib.cached() or {}
        g = next((x for x in lib.get("games") or [] if x["appid"] == appid), None)
        if not appid.isdigit() or not g or acc not in g["accounts"] + g.get("family", []):
            return None
        if acc not in {a["id"] for a in steamlib.accounts()}:
            return None
        mods = bool(game.get("mods"))            # [v5.1] тумблер «Моды»: накладывать и сохранять — одно и то же
        cloudgames.remember_mods(appid, mods)
        if "gdata" in game:                      # [v5.6] «Хранить игру в облаке» — с карточки игры перед арендой
            cloudgames.remember_gdata(appid, bool(game.get("gdata")))
        if "recipe" in game and cloudgames.recipe_for(appid):
            cloudgames.remember_recipe(appid, bool(game.get("recipe")))
        return {"appid": appid, "account": acc, "name": g["name"], "cap": g.get("cap") or g.get("header") or "",
                "hero": g.get("hero") or "", "header": g.get("header") or "", "mods": mods,
                "gdata": appid in cloudgames.gdata_list(), "recipe": mods and appid in cloudgames.recipes_list()}

    def steam_accounts(self):
        return {"accounts": steamlib.accounts()}

    def steam_login(self, title="Steam"):
        """Окно входа Steam (страница самой Steam: QR или логин с паролем). Ждёт, пока человек войдёт или закроет
        окно (до STEAM_LOGIN_MIN). Долгий пропуск перехватывается в окне (steamlib.HOOK) и сразу уходит в Связку
        ключей — в страницу он не попадает. {"ok", "account"} / {"cancelled"}."""
        if not self._steam_lock.acquire(blocking=False):
            return {"error": {"key": "steam.login_open", "params": {}, "text": "login window is open"}}
        try:
            self._clear_web()                    # прошлый вход в Steam не должен «узнать» человека — нужен чистый
            # Сначала своя страница «открываю…», потом Steam: до steamcommunity.com бывает очень медленно (облачный
            # ПК 2026-10-08: ~2 мин и в Chrome) — пустое белое окно выглядело зависшим. Прежняя страница видна,
            # пока Steam не ответит
            tr = notify.texts(settings.get("language") or settings.get("ui_language"))
            w = webview.create_window(str(title)[:60], html=_loading_page(tr["steam.login_loading"]), width=900,
                                      height=760)
            closed, shown = threading.Event(), threading.Event()
            w.events.closed += closed.set
            w.events.loaded += shown.set
            shown.wait(10)
            if not closed.is_set():
                w.load_url(steamlib.LOGIN_URL)
            token, until = None, time.time() + STEAM_LOGIN_MIN * 60
            while not closed.is_set() and time.time() < until:
                time.sleep(0.4)
                try:
                    w.evaluate_js(steamlib.HOOK)    # после каждого перехода страницы — заново
                    token = w.evaluate_js(steamlib.TAKE)
                except Exception:                   # noqa: BLE001 — страница ещё грузится или окно закрыли
                    continue
                if token:
                    break
            if not closed.is_set():
                w.destroy()
            self._clear_web()                    # cookie сайта Steam в окне не оставляем
            if not isinstance(token, str) or not token:
                return {"cancelled": True}
            acc = steamlib.add_account(token)
            settings.applog(f"steam: аккаунт …{acc['id'][-4:]} привязан")
            # [0.4.5] Игры нового аккаунта — в библиотеку сразу (секунды; остальные аккаунты — из прошлого списка):
            # раньше после входа из «Настройки» библиотека показывала их только после полного обновления
            try:
                steamlib.library(only=acc["id"])
            except Exception:                   # noqa: BLE001 — вход удался; список догонит обычное обновление
                _log_error("steam_login library")
            return {"ok": True, "account": acc}
        finally:
            self._steam_lock.release()

    def steam_logout(self, sid):
        steamlib.remove_account(sid)
        settings.applog(f"steam: аккаунт …{str(sid)[-4:]} отвязан")
        return {"ok": True, "accounts": steamlib.accounts()}

    def _clear_web(self):
        """Стереть cookie и данные сайтов в окнах приложения (хранилище у всех окон одно; сама страница vastgame
        ничего в нём не держит). Асинхронно — короткая пауза."""
        if self._window:
            try:
                self._window.clear_cookies()
                time.sleep(0.5)
            except Exception:                       # noqa: BLE001
                pass

    # ---- Вопросы машины (wolf-setup.sh v4.6): «новая игра — синхронизировать?», «игру удалили — удалить из облака?»
    def machine_asks(self):
        """Вопросы машины сейчас (главный экран, карточка, «Закончить») и язык окна — для карточки."""
        _, items = asks.pending()
        return {"asks": items, "lang": settings.get("language") or settings.get("ui_language")}

    def ask_answer(self, kind, archive, yes):
        """Ответ человека: машине (она выгрузит / не выгрузит игру, удалит её из облака) и в выбор игр. Ответ
        проверяем: вопрос должен исчезнуть со страницы машины."""
        with self._answer_lock:
            ip, items = asks.pending()
            if not any(a["kind"] == kind and a["archive"] == archive for a in items):
                return {"ok": True, "left": len(items)}          # уже ответили (в другом окне или раньше)
            asks.answer(ip, kind, archive, bool(yes), on_notice=self._ssh_notice)
            _, items = asks.pending()
            if any(a["kind"] == kind and a["archive"] == archive for a in items):
                raise VastgameError("Машина не приняла ответ", key="ask.failed", code=0, out="")
        if not items:                            # закрыть после ответа странице: destroy() изнутри вызова, которого
            threading.Timer(0.3, self.card_close).start()   # ждёт сама карточка, вешал её (живой случай 2026-10-09)
        return {"ok": True, "left": len(items)}

    def card_close(self):
        """Закрыть карточку (ответили на всё или закрыли без ответа) и вернуть впереди то, что было (Moonlight)."""
        card, self._card = self._card, None
        if card:
            card.destroy()
        _activate(self._front)
        self._front = None

    def _show_card(self):
        """Карточка вопроса поверх всех окон, в том числе полноэкранного Moonlight, и с фокусом: Moonlight, потеряв
        фокус, отпускает мышь. Уже открыта — сама обновит список."""
        if self._card:
            return
        self._front = _frontmost()
        self._card = webview.create_window("VastGame", str(HERE / "ui" / "ask.html"), js_api=self, width=460,
                                           height=300, resizable=False, frameless=True, easy_drag=True,
                                           on_top=True, hidden=True, transparent=True)
        card = self._card
        card.events.loaded += lambda: _float(card)
        card.events.closed += lambda: self._card_closed(card)

    def _card_closed(self, card):
        if self._card is card:
            self._card = None
        if self._card_only:
            _activate(self._front)

    def _watch_asks(self):
        """Окно открыто: раз в ASK_POLL с — есть ли новые вопросы машины. Новый — уведомление со звуком и карточка
        (один раз на вопрос; закрытую без ответа покажем ещё раз перед «Закончить» и на главном экране)."""
        self._window.events.shown.wait()         # карточку создаём, когда окно уже работает
        while True:
            try:
                _, items = asks.pending()
                new = asks.fresh(items)
                if new:
                    asks.mark_seen(new)
                    asks.announce(new)
                    asks.show_card()                 # отдельным «фоновым» процессом — поверх полноэкранного Moonlight
            except Exception:                    # noqa: BLE001 — следить дальше, что бы ни случилось
                _log_error("watch_asks")
            time.sleep(ASK_POLL)

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
            for _ in range(2):                  # разовая заминка Tailscale (Windows, 2026-10-08) — ещё два раза
                if node:
                    break
                time.sleep(2)
                node = wolfnode.ts_node()
            if not node:
                settings.applog(f"moonlight: {host} — машины нет в Tailscale")
                return {"error": {"key": "pair.no_machine", "params": {}, "text": "машины нет в Tailscale"}}
            pairing.pair(host, node[1], on_notice=self._ssh_notice)
            # Живой случай 2026-09-27 (друг): связка прошла, а трансляция сразу после неё не пошла, и Moonlight связку
            # «не видел» — со второго нажатия (ещё одна связка) заработало. Теперь проверяем сами и повторяем за человека
            if not self._sees_pairing(host):
                settings.applog("moonlight: после связки Moonlight не видит машину — связываю ещё раз; журнал Moonlight: "
                                + moonlight.log_tail())
                pairing.pair(host, node[1], on_notice=self._ssh_notice)
                if not self._sees_pairing(host):
                    settings.applog("moonlight: и после второй связки не видит; журнал Moonlight: " + moonlight.log_tail())
        args = self._stream_args()
        settings.applog(f"moonlight: трансляция {host} «{app}», разрешение/частота/битрейт: {args}")
        moonlight.stream(host, app, *args)
        return {"ok": True}

    @staticmethod
    def _sees_pairing(host, tries=3, pause=3):
        """Moonlight отвечает списком приложений машины — связка у него есть (после связки бывает не сразу)."""
        for i in range(tries):
            if moonlight.apps(host) is not None:
                return True
            if i < tries - 1:
                time.sleep(pause)
        return False

    def _ssh_notice(self, text):
        """Tailscale SSH просит подтвердить вход (режим «check») — открыть его страницу в браузере
        самим, один раз: окно ждёт ответа и ничего показать не может. Только страницы Tailscale."""
        m = re.search(r"https://login\.tailscale\.com/\S+", text or "")
        if m and m.group(0) not in self._opened:
            self._opened.add(m.group(0))
            webbrowser.open(m.group(0))

    # ---- Разрешение и битрейт трансляции (по умолчанию — родное разрешение экрана и «авто»)
    def _native(self):
        when, native = self._display
        if time.time() - when > 60:          # монитор могли подключить или отключить
            native = moonlight.native_display()
            self._display = (time.time(), native)
        return native

    def stream_settings(self):
        """Варианты разрешения (родное — первым) и битрейта и выбор человека."""
        native = self._native()
        opts = [{"value": "native" if native and i == 0 else f"{w}x{h}", "w": w, "h": h,
                 "native": bool(native) and i == 0} for i, (w, h) in enumerate(moonlight.resolutions(native))]
        if not native:                        # экран не узнать — первым «как в настройках Moonlight»
            opts.insert(0, {"value": "moonlight", "w": None, "h": None, "native": False})
        saved = settings.get("stream") or {}
        res = saved.get("resolution", "native")
        if res not in [o["value"] for o in opts]:
            res = opts[0]["value"]
        bitrate = saved.get("bitrate", 0) if saved.get("bitrate", 0) in [0] + moonlight.BITRATES else 0
        return {"native": native, "resolutions": opts, "resolution": res,
                "bitrates": moonlight.BITRATES, "bitrate": bitrate}

    def save_stream_settings(self, resolution, bitrate):
        s = self.stream_settings()
        if resolution not in [o["value"] for o in s["resolutions"]] or bitrate not in [0] + moonlight.BITRATES:
            return {"error": {"key": None, "params": {}, "text": "bad arguments"}}
        settings.set("stream", {"resolution": resolution, "bitrate": bitrate})
        return {"ok": True}

    def _stream_args(self):
        """(размер, частота, битрейт Мбит/с) для Moonlight; None — пусть решает сам Moonlight."""
        s = self.stream_settings()
        native, res = s["native"], s["resolution"]
        size = ((native["w"], native["h"]) if res == "native"
                else tuple(int(x) for x in res.split("x")) if "x" in res else None)
        return size, (native["fps"] if native and size else None), (s["bitrate"] or None)

    def sunshine_login(self):
        """Логин кабинета Sunshine, который задало приложение, — показать человеку по его просьбе."""
        pw = keystore.get("SUNSHINE_PASSWORD")
        return {"user": pairing.USER, "password": pw} if pw else {"user": None}

    # ---- обновление
    def update_status(self):
        """Есть ли версия новее: {"current", "available", "latest", "page"} — по последней проверке, сразу.
        Сама проверка — в фоне (updater.Checker), окно сети не ждёт."""
        return self._updates.status()

    def update_prepare(self):
        """«Обновить»: скачать новую версию и проверить её (подпись описания, размер и SHA-256 архива, подпись
        кода, самопроверка) — в окне, ход через progress(). Можно и во время игры: машину это не трогает."""
        if self._local_busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        job = self._job = Job("update")

        def run():
            shown = [-1]

            def on_progress(got, total):
                percent = got * 100 // total
                if percent != shown[0]:            # событие — только когда меняется процент (не больше 100)
                    shown[0] = percent
                    job.say(f"скачано {percent}%", key="update.downloading", percent=percent)
            try:
                job.result = self._update_ready = updater.prepare(on_progress=on_progress, say=job.say)
            except VastgameError as e:
                job.error = error_data(e)
            except Exception as e:                 # noqa: BLE001 — показать любую причину
                _log_error("update_prepare")
                job.error = jobs.unexpected(e)
            finally:
                job.done = True

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def update_install(self):
        """Поставить скачанную и проверенную версию (страница спросила): дождаться конца «Играть»/«Закончить»,
        поменять местами с прежней, запустить сторожа и закрыть окно — новую версию откроет сторож
        (updater.install). Не вышло — ошибка в progress(), приложение не тронуто."""
        ready = self._update_ready
        if not ready:
            return {"error": {"key": "update.err.not_ready", "params": {}, "text": "not ready"}}
        if self._local_busy():
            return {"error": {"key": "busy", "params": {}, "text": "busy"}}
        job = self._job = Job("update")

        def run():
            try:
                said = False
                while (jobs.state() or {}).get("active"):    # «Играть»/«Закончить» идёт в фоне — ждём конца
                    if not said:
                        job.say("жду окончания операции", key="update.waiting")
                        said = True
                    time.sleep(2)
                job.say("ставлю новую версию", key="update.installing", version=ready["version"])
                updater.install(ready["app"], ready["version"])
            except VastgameError as e:
                job.error = error_data(e)
                job.done = True
                return
            except Exception as e:                 # noqa: BLE001 — показать любую причину
                _log_error("update_install")
                job.error = jobs.unexpected(e)
                job.done = True
                return
            self._exit()

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def _exit(self):
        """Выйти сразу после замены, без уборки: файлы в папке приложения уже новой версии, прежнему окну
        ничего нельзя из неё загружать (страница уже пишет «окно перезапустится»). Новую версию откроет сторож.
        Windows: сначала убрать значок трея — иначе он «висит» до наведения мыши."""
        if self._tray:
            self._tray.stop()
        os._exit(0)

    # ---- прочее
    def open_url(self, url):
        """Открыть адрес в браузере — только http(s): кабинет Sunshine, страница статуса, вход Tailscale."""
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)

    def force_close(self):
        """Человек подтвердил на странице: закрыть окно, хотя операция ещё идёт."""
        self._force_close = self._quitting = True
        if self._tray:
            self._tray.stop()
        self._window.destroy()

    # ---- Windows: трей
    def _tray_open(self):
        """Щелчок по значку в трее, «Открыть VastGame» или повторный запуск программы — показать окно."""
        def go():
            self._window.show()
            self._window.restore()
            try:
                from System import Action
                form = self._window.native
                form.Invoke(Action(lambda: (form.Activate(), form.BringToFront())))
            except Exception:                              # noqa: BLE001 — показано и так
                pass
        threading.Thread(target=go, daemon=True).start()

    def _tray_quit(self):
        """«Выйти» в трее: закрыть по-настоящему. Идёт короткая операция в окне — показать окно и спросить, как всегда."""
        if self._local_busy():
            self._tray_open()
            threading.Thread(target=lambda: self._window.evaluate_js("askClose()"), daemon=True).start()
            return
        self._quitting = True
        self._tray.stop()
        threading.Thread(target=self._window.destroy, daemon=True).start()

    def _watch_show(self):
        """Повторный запуск VastGame, пока он в трее (Windows): второй процесс оставляет файл-просьбу и выходит."""
        req = settings.config_dir() / "show.request"
        while True:
            if req.exists():
                req.unlink(missing_ok=True)
                self._tray_open()
            time.sleep(1)

    def _on_closing(self):
        """Закрытие окна. «Играть»/«Закончить» идут в фоновом процессе — окно закрывается спокойно.
        Идёт короткая операция в самом окне (убрать игру, установка) — спросить на странице.
        Спрашиваем страницей, а не системным диалогом: этот обработчик работает в главном потоке,
        и системный диалог из него повесил бы окно."""
        if self._tray and self._tray.icon is not None and not self._quitting:
            # Windows: окно — в трей, программа продолжает работать (и короткие операции окна тоже)
            threading.Thread(target=lambda: (self._window.hide(), self._tray.hidden()), daemon=True).start()
            return False
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


def _traffic(o):
    """Цена трафика машины в $ за ТБ: tb_down — скачивание на машину (игры и сохранения из облака), tb_up — с машины
    (трансляция Moonlight и выгрузка в облако). Ставку задаёт хост; у Docker-машин Vast её списывает, у KVM — нет."""
    return {"tb_down": round((o.get("inet_down_cost") or 0) * 1000, 2), "tb_up": round((o.get("inet_up_cost") or 0) * 1000, 2)}


def _frontmost():
    """Mac: bundle id приложения, которое сейчас впереди (чтобы вернуть его после карточки), иначе None."""
    if sys.platform != "darwin":
        return None
    try:
        import AppKit
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        bid = app and app.bundleIdentifier()
        return None if bid == AppKit.NSBundle.mainBundle().bundleIdentifier() else bid
    except Exception:                            # noqa: BLE001 — не вышло — просто не вернём
        return None


def _activate(bundle_id):
    """Вернуть впереди приложение (Moonlight) после карточки — там человек снова нажмёт, и мышь вернётся в игру."""
    if sys.platform != "darwin" or not bundle_id:
        return
    try:
        import AppKit
        apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
        if apps:           # сообщение другому приложению — можно не из главного потока (процесс карточки уже выходит)
            apps[0].activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
    except Exception:                            # noqa: BLE001
        pass


def _front_windows(window):
    """Windows: карточка поверх всех окон (TopMost) и на передний план с фокусом — тогда Moonlight отпускает мышь.
    Фоновой программе (служба автозапуска) Windows фокус может не дать — тогда подсветит её значок; поверх окон
    карточка будет всё равно, а уведомление со звуком придёт в любом случае."""
    try:
        from System import Action
        from System.Drawing import Point
        from System.Windows.Forms import Cursor, Screen
        form = window.native

        def front():
            # Как на Mac: по центру экрана, где мышь, чуть выше середины
            area = Screen.FromPoint(Cursor.Position).WorkingArea
            form.Location = Point(int(area.X + (area.Width - form.Width) / 2),
                                  int(area.Y + area.Height * 0.38 - form.Height / 2))
            form.TopMost = True
            form.Activate()
            form.BringToFront()
        form.Invoke(Action(front))
    except Exception:                                  # noqa: BLE001 — не вышло: карточка просто показана
        settings.applog("карточка: не удалось вывести на передний план")


def _float(window):
    """Mac: поднять карточку над всеми окнами, в том числе над полноэкранной игрой (уровень «щита» экрана — выше
    него Moonlight в полноэкранном режиме не бывает), показать на всех рабочих столах (и в полноэкранном), по центру
    экрана с мышью, и забрать фокус — тогда Moonlight отпускает мышь."""
    if sys.platform == "win32":
        window.show()
        _front_windows(window)
        return
    if sys.platform != "darwin":
        window.show()
        return
    import AppKit
    import Quartz
    from PyObjCTools import AppHelper

    def go():
        w = window.native
        if w is None:
            return
        # «Фоновое» приложение без значка в Dock — только его окна macOS пускает на чужой полноэкранный стол.
        # Здесь, а не до запуска: pywebview при загрузке сам ставит обычный режим (setActivationPolicy_(0))
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        w.setLevel_(Quartz.CGShieldingWindowLevel() + 1)
        w.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                                 | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        w.setHidesOnDeactivate_(False)
        mouse = AppKit.NSEvent.mouseLocation()
        screen = next((sc for sc in AppKit.NSScreen.screens()
                       if AppKit.NSPointInRect(mouse, sc.frame())), AppKit.NSScreen.mainScreen())
        f, size = screen.frame(), w.frame().size
        w.setFrameOrigin_((f.origin.x + (f.size.width - size.width) / 2,
                           f.origin.y + f.size.height * 0.62 - size.height / 2))
        w.makeKeyAndOrderFront_(None)
        w.orderFrontRegardless()
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        AppHelper.callAfter(report)

    def report():
        # Где оказалась карточка — для разбора (поверх Moonlight её проверить можно только вживую)
        w = window.native
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        settings.applog(f"карточка: видна={bool(w.isVisible())}, на текущем столе={bool(w.isOnActiveSpace())}, "
                        f"ключевая={bool(w.isKeyWindow())}, уровень={w.level()}, режим={AppKit.NSApp.activationPolicy()}, "
                        f"впереди={front and front.bundleIdentifier()}")
    AppHelper.callAfter(go)


def main():
    autostart.refresh()      # служба стоит, но проект переехал — поправить пути (выключенную не трогаем)
    lock = open(asks.WINDOW_LOCK, "a")   # «окно открыто» — для службы: карточку вопроса покажет окно
    try:
        first = filelock.try_lock(lock)
    except OSError:
        first = True                         # не понять — открыть окно, как раньше
    if not first and sys.platform == "win32":
        # VastGame уже работает (окно или трей) — показать его, а не открывать второе
        (settings.config_dir() / "show.request").touch()
        return
    api = Api()
    window = webview.create_window("VastGame", str(HERE / "ui" / "index.html"), js_api=api,
                                   width=1180, height=780, min_size=(760, 600))   # [2026-10-15] шире — вид «как Big Picture»
    api._window = window
    window.events.closing += api._on_closing
    if sys.platform == "win32":
        import tray
        api._tray = tray.Tray(window, api._tray_open, api._tray_quit)
        started = []

        def put_tray():                      # «loaded» бывает и при перезагрузке страницы — значок ставим один раз
            if not started:
                started.append(True)
                api._tray.start()
        window.events.loaded += put_tray
        threading.Thread(target=api._watch_show, daemon=True).start()
    # Свой поток, а не webview.start(func): поток pywebview не фоновый и не дал бы процессу выйти после окна
    threading.Thread(target=api._watch_asks, daemon=True).start()
    webview.start(debug=bool(os.environ.get("VASTGAME_DEBUG")))


def card_main():
    """Карточка вопроса машины отдельным процессом («app_main.py ask» — запускают окно и служба автозапуска): одна
    карточка поверх всего, выход, когда её закроют. «Фоновое» приложение без значка в Dock. Уже открыта — ничего."""
    with open(asks.CARD_LOCK, "a") as lock:
        try:
            if not filelock.try_lock(lock):
                return
        except OSError:
            return
        if sys.platform == "darwin":
            # «Фоновое» приложение с самого запуска: переключение уже работающего macOS не учитывает (живой тест
            # 2026-10-01). pywebview ставит обычный режим при загрузке своего модуля Mac — загрузить его первым
            import webview.platforms.cocoa  # noqa: F401
            import AppKit
            AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                AppKit.NSApplicationActivationPolicyAccessory)
        api = Api()
        api._card_only = True
        api._show_card()
        webview.start()


if __name__ == "__main__":
    main()

# jobs.py — долгие операции с машиной и их ход.
#
# «Играть» и «Закончить» идут в ОТДЕЛЬНОМ фоновом процессе (app_main.py job play|stop ...), который
# не зависит от окна: окно можно закрыть — запуск или выгрузка с удалением доведутся до конца,
# а в конце придёт системное уведомление. Процесс пишет свой ход в job.json в папке настроек,
# окно читает этот файл (Api.session_progress) и при повторном открытии подключается снова.
#
# Что процесс ещё работает, узнаём по блокировке job.lock: он держит её, пока жив. Блокировку
# снимает сама система, когда процесс завершился (даже аварийно), — старый номер процесса
# или перезагрузка Mac не обманут.
#
# Вопросов «да/нет» у этих операций нет (решение 2026-09-24): машину, которая не запустилась,
# и машину после подтверждённой выгрузки ядро удаляет само — окно может быть закрыто.
#
# Короткие операции окна (убрать игру из облака, установка программ, вход в Google) идут
# потоком внутри окна — обычный Job.

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import keyring.errors

import core
import filelock
import market
import notify
import paths
import settings
from settings import config_dir
from vastapi import VastgameError

HERE = Path(__file__).resolve().parent
STATE = config_dir() / "job.json"     # ход последней операции play/stop
LOCK = config_dir() / "job.lock"      # держит работающий фоновый процесс
LOG = config_dir() / "job.log"        # вывод фонового процесса (на случай неожиданной ошибки)
CANCEL = config_dir() / "job.cancel"  # человек нажал «Отменить запуск» — фоновый процесс увидит и закончит
START_GRACE = 30                      # с, за которые фоновый процесс должен успеть стартовать
MAX_RESUMES = 3                       # сколько раз продолжать прерванную операцию (защита от петли)
DISK_MIN, DISK_MAX = 50, 1000         # пределы ползунка диска, ГБ (система и Steam занимают ~30 ГБ)


def error_data(e):
    """Ошибку — странице: ключ перевода и данные (перевод делает страница), плюс русский текст."""
    return {"key": getattr(e, "key", None), "params": getattr(e, "params", {}), "text": str(e)}


def unexpected(e):
    """Неожиданная ошибка — странице. Отказ в доступе к Связке ключей — понятным текстом: так бывает,
    когда ключи прежней версии переносятся в новое место (keystore.move_old, macOS спрашивает про каждый),
    а человек нажал «Запретить»; или когда Связка ключей не ответила (keystore.KeystoreError)."""
    if isinstance(e, keyring.errors.KeyringError):
        return {"key": "keychain.denied", "params": {}, "text": f"{type(e).__name__}: {e}"}
    return {"key": None, "params": {}, "text": f"{type(e).__name__}: {e}"}


class Job:
    """Операция с машиной. Это же и «ui» для ядра: say/stage/archive/confirm/tailscale."""

    def __init__(self, kind):
        self.kind = kind                 # "play", "stop", "games", "install", "google"
        self.started = time.time()
        self.events = []                 # сообщения по порядку: {level, key, params, text}
        self.stage_key = None
        self.archives = {}               # {имя: [состояние, текст wolf]}
        self.question = None             # {id, key, params, text} — пока ждём ответа
        self.result = None
        self.error = None
        self.done = False
        self.ended = None
        self._lock = threading.Lock()
        self._answered = threading.Event()
        self._answer = False
        self._qid = 0

    def _changed(self):
        """Что-то изменилось (для процесса в фоне — сохранить в файл)."""

    # ---- ui для ядра
    def say(self, text, level="info", key=None, **params):
        with self._lock:
            self.events.append({"level": level, "key": key, "params": params, "text": text})
        self._changed()

    def stage(self, key, text):
        with self._lock:
            self.stage_key = key
            self.events.append({"level": "stage", "key": "stage." + key, "params": {}, "text": text})
        self._changed()

    def archive(self, name, state, text, progress=None):
        """progress — ход архива {done, total, speed} (wolf-setup.sh v4.6), пока он качается или выгружается."""
        with self._lock:
            if self.archives.get(name) == [state, text, progress]:
                return
            self.archives[name] = [state, text, progress]
        self._changed()

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
        self._changed()

    def finish(self, result=None, error=None):
        self.result, self.error, self.ended, self.done = result, error, time.time(), True
        self._changed()

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


class FileJob(Job):
    """Job фонового процесса: после каждого изменения записывает себя в job.json — окно читает оттуда."""

    def __init__(self, kind, pid=None, args=None, carry=None):
        """carry — прежнее содержимое job.json: продолжаем прерванную операцию с теми же
        событиями, архивами и данными (номер машины), чтобы окно видело один непрерывный ход."""
        super().__init__(kind)
        self.pid = pid
        self.args = dict(args or {})
        self.meta = {}                   # что уже сделано: {"iid", "t0", "offer"} после аренды
        self.resumes = 0
        self.spawned = time.time()
        self._opened = set()             # ссылки, уже открытые в браузере
        if carry:
            self.started, self.events, self.archives = carry["started"], carry["events"], carry["archives"]
            self.stage_key, self.meta = carry.get("stage"), carry.get("meta") or {}
            self.resumes, self.spawned = carry.get("resumes", 0), carry.get("spawned", self.spawned)
        self._changed()

    def set_meta(self, **kw):
        self.meta.update(kw)
        self._changed()

    def cancelled(self):
        """Человек отменил запуск в окне (флаг-файл: окно и фоновый процесс — разные процессы)."""
        return self.kind == "play" and CANCEL.exists()

    def say(self, text, level="info", key=None, **params):
        super().say(text, level, key, **params)
        # Ссылку одобрения машины в Tailscale открываем в браузере сами — окно может быть закрыто
        if key == "core.ts_approve" and params.get("url") and params["url"] not in self._opened:
            self._opened.add(params["url"])
            import webbrowser
            webbrowser.open(params["url"])

    def confirm(self, question, key=None, **params):
        # Спросить некого (окно может быть закрыто). Вопросов у play/stop нет; если ядро всё же
        # спросит — безопасный ответ «нет», и это видно в ходе операции
        self.say(f"(вопрос без ответа, считаю «нет»: {question})", "warn", key="progress.auto_no")
        return False

    def _changed(self):
        with self._lock:
            data = {"kind": self.kind, "pid": self.pid, "started": self.started, "stage": self.stage_key,
                    "events": self.events, "archives": self.archives, "result": self.result,
                    "error": self.error, "done": self.done, "ended": self.ended, "seen": False,
                    "args": self.args, "meta": self.meta, "resumes": self.resumes, "spawned": self.spawned}
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False))
            os.replace(tmp, STATE)        # окно никогда не прочитает наполовину записанный файл


# ------------------------------------------------------------------ операции ---

def disk_size(disk, tpl):
    """Размер диска: с ползунка, иначе последний выбранный, иначе из шаблона; в пределах."""
    disk = disk or settings.get("disk") or core.template_disk(tpl)
    return max(DISK_MIN, min(DISK_MAX, int(disk)))


# Забег (сразу 3 машины, остаётся первая запустившаяся) убран (решение 2026-09-27): машину по факту выбирал не человек,
# а «кто первым запустится» — у друга вместо выбранной RTX 4090 осталась RTX 5090 за $0.69/ч. Теперь арендуется только
# выбранная; не запустилась — замена следующей из списка человека, но только «не хуже» (видеокарта не слабее, пинг не
# больше — это отбирает страница, цена в час и трафик не выше — перепроверяется здесь по свежим ценам); таких нет —
# ошибка core.no_spare, человек выбирает сам.
RACE = 1             # машин сразу
MAX_RENTS = 5        # всего аренд за один запуск: выбранная и до четырёх замен ([2026-10-15] было 3 — не хватало)
SPARE_MARGIN = 1.2   # [2026-10-15] замена — «похожая»: цена в час и трафик до +20% к выбранной (было «не дороже» —
                     # после сбоя часто не оставалось ни одной замены)
RACE_POLL = 10       # с: как часто спрашивать Vast о состоянии машин
GONE_WAIT = 60       # с: ждать, пока удалённая машина исчезнет у Vast, прежде чем брать другую


def run_play(job, offer_id, sync=None, disk=None, lang=None, spare=None, docker=False, hint=None):
    """Аренда (человек уже ответил «да» в окне) и ожидание готовности.

    На Vast часть KVM-хостов подолгу (или навсегда) висит в Create/Loading: машину, которую Vast не запустил
    за VAST_START_MIN или хост сломал, удаляем (до running Vast берёт только за диск — ~$0.001) и берём
    следующую из spare — списка «не хуже выбранной», который отобрала страница (_race, RACE машин сразу).
    docker — вкладка «Docker» (основная с 0.3.1): контейнер из образа vastgame-desktop вместо KVM; машину
    без работающего NVENC wait_ready заменяет так же, как не запустившуюся (core.try_another)."""
    try:
        tpl = core.template()
        size = disk_size(disk, tpl)
        m = getattr(job, "meta", {})
        if m.get("iid"):
            # Продолжение после перерыва: победитель уже выбран — только досмотреть запуск
            if m["iid"] not in [i["id"] for i in core.list_instances()]:
                raise VastgameError(f"Машины {m['iid']} уже нет — её удалили, пока компьютер был выключен.",
                                    key="progress.resume_gone", id=m["iid"])
        elif not m.get("race"):
            core.require_no_machine()                        # ещё раз — прямо перед арендой
            settings.set("disk", size)
        queue = [int(offer_id)] + [int(x) for x in spare or []]
        while True:
            m = getattr(job, "meta", {})
            if m.get("iid"):
                iid, t0, o = m["iid"], m["t0"], m["offer"]
            else:
                iid, t0, o = _race(job, queue, tpl, size, sync, docker, lang, hint)
            try:
                r = core.wait_ready(iid, t0, o, job, docker=docker)
                break
            except VastgameError as e:
                if e.key != "core.try_another":              # отмена, ошибка после running и т.п.
                    raise
                # Победитель сломался ещё у Vast (редко) — новый забег из оставшихся
                _set_meta(job, iid=None, tried=list(m.get("tried") or []) + [o.get("machine_id")])
                if not _gone(iid):
                    raise VastgameError(f"Машина {iid} не удалилась — другую не беру, чтобы не платить за две. "
                                        "Удали её на главном экране или на сайте Vast.",
                                        key="core.switch_blocked", id=iid) from None
        job.finish(result={"links": r["links"], "minutes": r["minutes"], "boot": list(r["boot"]),
                           "notes": [{"level": lv, "key": k, "params": p, "text": tx}
                                     for lv, tx, k, p in core.identity_notes(r)]})
    except VastgameError as e:
        job.finish(error=error_data(e))
    except Exception as e:                               # неожиданное — тоже показать, не молчать
        job.finish(error=unexpected(e))


def _set_meta(job, **kw):
    if hasattr(job, "set_meta"):
        job.set_meta(**kw)
    else:
        job.meta = {**getattr(job, "meta", {}), **kw}


def _where(o):
    return (o.get("geolocation") or "").strip(", ")


def _traffic(o):
    """Цена трафика, $ за ГБ (дороже из двух сторон)."""
    return max(o.get("inet_up_cost") or 0, o.get("inet_down_cost") or 0)


def _race(job, queue, tpl, size, sync, docker=False, lang=None, hint=None):
    """Запустить наперегонки до RACE машин из очереди (id предложений); вернуть (iid, t0, offer)
    первой дошедшей до running, остальные удалить. Зависшую дольше VAST_START_MIN или сломанную —
    удалить, пометить хост и поставить вместо неё следующую. Состояние забега — в meta: окно
    отменяет все машины, а продолжение после перерыва подхватывает забег с того же места."""
    m = getattr(job, "meta", {})
    racers = list(m.get("race") or [])                 # [{"iid", "t0", "offer", "errors"}]
    tried = list(m.get("tried") or [])                 # хосты, которые не запустились
    rents = m.get("rents", len(racers))
    limit = m.get("limit")                             # цена выбранной машины: замены не дороже
    used = set(tried) | {r["offer"].get("machine_id") for r in racers} | set(m.get("used") or [])
    first = limit is None                             # ещё ни одной аренды — эта будет выбранной
    # Замена: этап «Vast» — только перед настоящей арендой. Иначе при «замен нет» ошибка вставала на первый этап,
    # хотя машина сломалась позже (экран хода Windows, 2026-10-08)
    announced = first or bool(racers)
    if announced:
        job.stage("vast", "Vast запускает машину")

    def save():
        _set_meta(job, race=racers, tried=tried, rents=rents, used=sorted(x for x in used if x is not None),
                  limit=limit)

    def drop(r, key, **params):
        """Убрать машину из забега: удалить у Vast (за неё списан только диск)."""
        racers.remove(r)
        core._delete_quietly(r["iid"])
        save()
        if key:
            job.say(key, "warn", key=key, gpu=r["offer"].get("gpu_name"), country=_where(r["offer"]), **params)

    def chosen_gone(oid):
        """[2026-10-15] Выбранную заняли, пока арендовали (15 из 203 запусков): не к списку, а похожую из списка
        человека — с теми же ограничениями, что у замены, от свежих данных выбранной (окно взяло их перед «Арендовать»).
        Без этих данных или без запасных — как раньше (к списку)."""
        nonlocal limit
        if not hint or not queue:
            return False
        limit = {"price": float(hint.get("price") or 0), "traffic": float(hint.get("traffic") or 0),
                 "down": float(hint.get("down") or 0)}
        save()
        job.say("Эту машину уже заняли — беру похожую", "warn", key="core.chosen_gone_next")
        return True

    def top_up():
        """Добрать машин до RACE из очереди. Первая — выбранная человеком: её и арендуем (даже если хост помечен —
        он видел пометку и подтвердил; заняли — сказать ему, а не подсовывать другую). Дальше — замены: свежие цена
        и наличие, другие хосты, не помеченные и не дороже выбранной (цена в час, в Docker — и трафик)."""
        nonlocal rents, limit, announced
        bad = set(market.bad_hosts())
        added = []
        while len(racers) < RACE and rents < MAX_RENTS and queue:
            oid = queue.pop(0)
            chosen = limit is None
            try:
                o = core.offer_by_id(int(oid), size, docker)
            except (VastgameError, ValueError, TypeError):
                if chosen:
                    if chosen_gone(oid):
                        continue
                    raise                              # выбранную заняли, пока подтверждали
                continue                               # замену заняли или не подходит — дальше по списку
            if not chosen and (o.get("machine_id") in used or str(o.get("machine_id")) in bad
                               or o.get("dph_total", 0) > limit["price"] * SPARE_MARGIN + 1e-6
                               or docker and _traffic(o) > limit["traffic"] * SPARE_MARGIN + 1e-9
                               or (o.get("inet_down") or 0) < (limit.get("down") or 0) * 0.7):   # интернет — не медленнее 70%
                continue
            if not announced:
                job.stage("vast", "Vast запускает машину")
                announced = True
            try:
                iid, t0 = core.rent(o["id"], core.rental_body(tpl, size, sync=sync, docker=docker, lang=lang), o)
            except VastgameError as e:
                if e.key == "core.offer_gone" and (not chosen or chosen_gone(oid)):
                    continue                           # замену (или выбранную — тогда похожая) только что заняли
                if not racers and not added:
                    raise                              # не арендовали ни одной — сказать почему
                # Например, не хватает баланса на ещё одну — ждём уже запущенные
                job.say(str(e), "warn", key="core.race_rent_failed", msg=getattr(e, "params", {}).get("msg", ""))
                queue.clear()
                break
            rents += 1
            if chosen:
                limit = {"price": o.get("dph_total", 0), "traffic": _traffic(o), "down": o.get("inet_down") or 0}
            used.add(o.get("machine_id"))
            r = {"iid": iid, "t0": t0, "offer": o, "errors": 0}
            racers.append(r)
            added.append(r)
            save()
        return added

    added = top_up()
    if not first:
        for r in added:
            job.say("Беру вместо неё", "info", key="core.race_added", gpu=r["offer"].get("gpu_name"),
                    country=_where(r["offer"]))
    while True:
        if getattr(job, "cancelled", lambda: False)():
            gone = ", ".join(str(r["iid"]) for r in list(racers))
            for r in list(racers):
                drop(r, None)
            raise VastgameError(f"Запуск отменён, машины {gone} удалены.", key="core.start_cancelled", id=gone)
        if not racers:
            if tried and rents >= MAX_RENTS:            # все попытки кончились — похоже, беда у Vast
                raise VastgameError(f"Не запустилась ни одна из {len(tried)} машин — похоже, у Vast сейчас "
                                    "проблемы с KVM. Попробуй позже или выбери машины в другой стране.",
                                    key="core.tries_exhausted_docker" if docker else "core.tries_exhausted",
                                    n=len(tried))
            if not rents:                              # ни одной аренды: выбранную заняли, похожих нет
                raise VastgameError("Машину уже заняли, а похожих в твоём списке нет — выбери другую.",
                                    key="core.chosen_gone_none")
            raise VastgameError("Машина не запустилась, а машин не хуже неё в списке нет — выбери другую.",
                                key="core.no_spare")
        try:
            now = {i["id"]: i for i in core.list_instances()}
        except VastgameError:
            now = None                                 # разовый сбой связи — спросить в следующий раз
        winner = None
        for r in list(racers):
            inst = (now or {}).get(r["iid"])
            if not inst:
                continue                               # только что арендована — Vast ещё не показывает
            st, msg = inst.get("actual_status"), (inst.get("status_msg") or "").strip()
            if st == "running":
                winner = r
                break
            r["errors"] = r["errors"] + 1 if "error" in msg.lower() else 0
            broken = st in ("exited", "offline", "unknown") or r["errors"] >= 2
            slow = time.time() - r["t0"] > core.VAST_START_MIN * 60
            if broken or slow:
                if slow and not broken:
                    why, wkey, wp = (f"Vast не запустил машину за {core.VAST_START_MIN} минут.", "core.fail_timeout",
                                     {"min": core.VAST_START_MIN})
                else:
                    why, wkey, wp = (f"Хост не смог запустить машину: {msg[:150] or st}", "core.fail_host",
                                     {"msg": msg[:150] or st})
                market.mark_bad(r["offer"].get("machine_id"), why, wkey, **wp)
                tried.append(r["offer"].get("machine_id"))
                if slow and not broken:
                    drop(r, "core.race_slow", min=core.VAST_START_MIN)
                else:
                    drop(r, "core.race_broken")
                for a in top_up():
                    job.say("Беру вместо неё", "info", key="core.race_added", gpu=a["offer"].get("gpu_name"),
                            country=_where(a["offer"]))
        if winner:
            losers = [r for r in racers if r is not winner]
            for r in losers:
                drop(r, None)                          # медленнее — не значит плохой: хост не помечаем
            o = winner["offer"]
            if losers:
                job.say("Первой запустилась машина", "ok", key="core.race_won", gpu=o.get("gpu_name"),
                        country=_where(o), min=max(1, round((time.time() - winner["t0"]) / 60)), n=len(losers))
            _set_meta(job, iid=winner["iid"], t0=winner["t0"], offer=o, race=[])
            return winner["iid"], winner["t0"], o
        time.sleep(RACE_POLL)


def _gone(iid, wait=None):
    """Удалённая машина исчезла у Vast (иначе вторую не арендуем — платили бы за две)."""
    end = time.time() + (GONE_WAIT if wait is None else wait)
    while True:
        if core._still_there(iid) is False:
            return True
        if time.time() > end:
            return False
        time.sleep(5)


def run_stop(job, lang=None, expected_id=None):
    """Выгрузить всё, проверить и удалить машину (удаление — только после подтверждённой выгрузки).

    expected_id — номер машины из неудавшейся выгрузки: ядро сверяет его до команды finish.
    """
    try:
        kw = {}
        if expected_id is not None:
            kw["expected_id"] = int(expected_id)
        r = core.stop(job, **kw)
        job.finish(result={"uploaded": r["uploaded"], "deleted": r["deleted"], "iid": r["iid"],
                           "gone": r.get("gone", False)})
    except VastgameError as e:
        if e.key == "core.no_instances" and getattr(job, "resumes", 0):
            # Продолжаем после перерыва, а машины уже нет: wolf finish закончил и удалил её сам
            # (удаляет он только после подтверждённой выгрузки) — или её удалили на сайте
            job.finish(result={"uploaded": [], "deleted": True, "iid": None, "gone": True})
        else:
            job.finish(error=error_data(e))
    except Exception as e:
        job.finish(error=unexpected(e))


RUNNERS = {"play": run_play, "stop": run_stop}


# ------------------------------------------------------------------ для окна ---

def running():
    """Работает ли фоновый процесс: он держит блокировку job.lock, пока жив."""
    try:
        f = open(LOCK, "a")
    except OSError:
        return False
    try:
        if not filelock.try_lock(f):
            return True
        filelock.unlock(f)
        return False
    finally:
        f.close()


def load():
    """Последняя операция из job.json или None."""
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def state():
    """Последняя операция для окна: её данные плюс active (идёт сейчас).

    Процесс только что запущен и ещё не взял блокировку — тоже active (первые START_GRACE секунд).
    Не done, но процесса нет — он умер неожиданно: помечаем ошибкой, чтобы окно не ждало вечно."""
    d = load()
    if not d:
        return None
    alive = running()
    fresh = d.get("pid") is None and time.time() - d.get("spawned", d.get("started", 0)) < START_GRACE
    if not d["done"] and not alive and not fresh:
        # Процесс пропал посреди операции: компьютер выключался, перезагружался или процесс упал.
        # Продолжаем с того же места; не получается — честно показать, что операция прервалась
        if resume(d):
            d = load()
        else:
            d.update(done=True, ended=time.time(),
                     error={"key": "progress.lost", "params": {}, "text": "фоновый процесс завершился"})
            save(d)
    d["active"] = not d["done"]
    return d


def resume(d):
    """Продолжить прерванную операцию d (из job.json) новым фоновым процессом. False — нельзя:
    слишком много попыток или запуск прервался до аренды (тогда продолжать нечего)."""
    if d.get("resumes", 0) >= MAX_RESUMES:
        return False
    if d["kind"] == "play" and not (d.get("meta") or {}).get("iid"):
        return False
    job = FileJob(d["kind"], args=d.get("args"), carry=dict(d, resumes=d.get("resumes", 0) + 1,
                                                                  spawned=time.time()))
    job.say("Операция прервалась (компьютер выключался или пропадала связь) — продолжаю.", "warn",
            key="progress.resuming")
    _launch(d["kind"], dict(d.get("args") or {}, _resume=True))
    return True


def cancel_play():
    """«Отменить запуск»: поставить флаг для фонового процесса. Номера уже арендованных машин —
    выбранной или всех машин забега (окно удалит их сразу, не дожидаясь процесса); [] — ещё ни
    одной (удалит сам процесс, когда арендует); None — отменять нечего."""
    d = state()
    if not d or d["kind"] != "play" or d["done"]:
        return None
    CANCEL.write_text(str(os.getpid()))
    m = d.get("meta") or {}
    return [m["iid"]] if m.get("iid") else [r["iid"] for r in m.get("race") or []]


def save(d):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v for k, v in d.items() if k != "active"}, ensure_ascii=False))
    os.replace(tmp, STATE)


def mark_seen():
    """Человек посмотрел результат — при следующем открытии окна его больше не показывать."""
    d = load()
    if d and d["done"] and not d.get("seen"):
        d["seen"] = True
        save(d)


def interrupted():
    """Есть прерванная операция: не закончена, процесса нет, и это не только что запущенная."""
    d = load()
    return bool(d and not d["done"] and not running()
                and not (d.get("pid") is None and time.time() - d.get("spawned", d.get("started", 0)) < START_GRACE))


def online():
    """Сеть готова для продолжения: Vast отвечает и Tailscale на этом компьютере подключён."""
    try:
        core.balance()
        return (paths.tailscale_status() or {}).get("BackendState") == "Running"
    except (VastgameError, OSError, ValueError, subprocess.SubprocessError):
        return False


def resume_when_online(log=lambda text: None, wait=600, step=15):
    """Для службы автозапуска: если операция прервалась — дождаться сети (после включения Mac
    интернет и Tailscale поднимаются не сразу) и продолжить. Нечего продолжать — выйти сразу."""
    if not interrupted():
        return False
    end = time.time() + wait
    while not online():
        if time.time() > end:
            log(f"[{time.strftime('%F %T')}] resume: сети нет {wait // 60} мин — попробую при следующем запуске")
            return False
        time.sleep(step)
    log(f"[{time.strftime('%F %T')}] resume: продолжаю прерванную операцию")
    state()        # сама проверка и продолжение — как при открытии окна
    return True


def watch_balance():
    """Для службы автозапуска: уведомление «баланс ниже $1», когда окно закрыто (во время игры
    человек смотрит в Moonlight, а не в окно). Нет ключа или сети — молча до следующего раза."""
    try:
        credit = core.balance()
        inst = core.list_instances()
    except (VastgameError, OSError):
        return
    notify.low_balance(credit, inst[0].get("dph_total") if inst else None)


def watch_asks():
    """Для службы автозапуска: у машины вопрос (новая / удалённая игра), а окно закрыто — уведомление со звуком и
    карточка поверх всех окон отдельным процессом (app_main.py ask). Окно открыто — оно само. Раз в 5 минут."""
    import asks
    try:
        if asks.window_open():
            return
        _, items = asks.pending()
        new = asks.fresh(items)
    except (VastgameError, OSError):
        return
    if new:
        asks.mark_seen(new)
        asks.announce(new)
        asks.show_card()


def spawn(kind, **args):
    """Запустить операцию в фоновом процессе, который переживёт закрытие окна. False — уже идёт другая."""
    if running() or (state() or {}).get("active"):
        return False
    FileJob(kind, args=args)  # сразу «началось» в job.json — окно не покажет результат прошлой операции
    _launch(kind, args)
    return True


def _launch(kind, args):
    """Запустить фоновый процесс (тесты подменяют его потоком с work)."""
    with open(LOG, "a") as log:
        subprocess.Popen(paths.self_cmd("job", kind, json.dumps(args)), cwd=HERE,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         **paths.detached())   # закрытие окна его не завершит


def work(kind, args, log=lambda text: None):
    """Сама операция: взять блокировку (держим её до конца), выполнить, прислать уведомление.
    log — куда писать начало и конец (фоновый процесс пишет в job.log)."""
    with open(LOCK, "a") as lock:
        if not filelock.try_lock(lock):
            return False                          # другая операция уже идёт — ничего не трогаем
        args = dict(args)
        carry = load() if args.pop("_resume", False) else None
        if not carry:
            CANCEL.unlink(missing_ok=True)        # отмена прошлого запуска к новому не относится
        log(f"[{time.strftime('%F %T')}] {kind} pid={os.getpid()}" + (" (продолжение)" if carry else ""))
        job = FileJob(kind, pid=os.getpid(), args=args, carry=carry)
        RUNNERS[kind](job, **args)
        log(f"[{time.strftime('%F %T')}] {kind} done, error={(job.error or {}).get('key')}")
        if (job.error or {}).get("key") != "core.start_cancelled":   # отменил сам человек — сообщать незачем
            notify.job_finished(kind, job.result, job.error, args.get("lang"))
        CANCEL.unlink(missing_ok=True)
    return True


def main(argv):
    """Точка входа фонового процесса (через app_main.py):
      job play|stop '<json с параметрами>' — выполнить операцию;
      resume — продолжить прерванную операцию, если она есть, и проверить баланс (служба автозапуска)."""
    log = lambda text: print(text, flush=True)   # noqa: E731 — вывод идёт в job.log
    if argv[0] == "resume":
        resume_when_online(log=log)
        watch_balance()
        watch_asks()
        return None
    if not work(argv[1], json.loads(argv[2]), log=log):
        return "другая операция уже идёт"
    return None

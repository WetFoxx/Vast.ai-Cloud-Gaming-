# jobs.py — долгие операции с машиной и их ход.
#
# «Играть» и «Закончить» идут в ОТДЕЛЬНОМ фоновом процессе (python jobs.py play|stop ...), который
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

import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import core
import notify
import settings
from settings import config_dir
from vastapi import VastgameError

HERE = Path(__file__).resolve().parent
STATE = config_dir() / "job.json"     # ход последней операции play/stop
LOCK = config_dir() / "job.lock"      # держит работающий фоновый процесс
LOG = config_dir() / "job.log"        # вывод фонового процесса (на случай неожиданной ошибки)
START_GRACE = 30                      # с, за которые фоновый процесс должен успеть стартовать
DISK_MIN, DISK_MAX = 50, 1000         # пределы ползунка диска, ГБ (система и Steam занимают ~30 ГБ)


def error_data(e):
    """Ошибку — странице: ключ перевода и данные (перевод делает страница), плюс русский текст."""
    return {"key": getattr(e, "key", None), "params": getattr(e, "params", {}), "text": str(e)}


def unexpected(e):
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

    def archive(self, name, state, text):
        with self._lock:
            if self.archives.get(name) == [state, text]:
                return
            self.archives[name] = [state, text]
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

    def __init__(self, kind, pid=None):
        super().__init__(kind)
        self.pid = pid
        self._changed()

    def confirm(self, question, key=None, **params):
        # Спросить некого (окно может быть закрыто). Вопросов у play/stop нет; если ядро всё же
        # спросит — безопасный ответ «нет», и это видно в ходе операции
        self.say(f"(вопрос без ответа, считаю «нет»: {question})", "warn")
        return False

    def _changed(self):
        with self._lock:
            data = {"kind": self.kind, "pid": self.pid, "started": self.started, "stage": self.stage_key,
                    "events": self.events, "archives": self.archives, "result": self.result,
                    "error": self.error, "done": self.done, "ended": self.ended, "seen": False}
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False))
            os.replace(tmp, STATE)        # окно никогда не прочитает наполовину записанный файл


# ------------------------------------------------------------------ операции ---

def disk_size(disk, tpl):
    """Размер диска: с ползунка, иначе последний выбранный, иначе из шаблона; в пределах."""
    disk = disk or settings.get("disk") or core.template_disk(tpl)
    return max(DISK_MIN, min(DISK_MAX, int(disk)))


def run_play(job, offer_id, sync=None, disk=None, lang=None):
    """Аренда (человек уже ответил «да» в окне) и ожидание готовности."""
    try:
        core.require_no_machine()                        # ещё раз — прямо перед арендой
        tpl = core.template()
        size = disk_size(disk, tpl)
        settings.set("disk", size)
        o = core.offer_by_id(int(offer_id), size)       # свежая цена и наличие
        iid, t0 = core.rent(o["id"], core.rental_body(tpl, size, sync=sync), o)
        r = core.wait_ready(iid, t0, o, job)
        job.finish(result={"links": r["links"], "minutes": r["minutes"], "boot": list(r["boot"]),
                           "notes": [{"level": lv, "key": k, "params": p, "text": tx}
                                     for lv, tx, k, p in core.identity_notes(r)]})
    except VastgameError as e:
        job.finish(error=error_data(e))
    except Exception as e:                               # неожиданное — тоже показать, не молчать
        job.finish(error=unexpected(e))


def run_stop(job, lang=None):
    """Выгрузить всё, проверить и удалить машину (удаление — только после подтверждённой выгрузки)."""
    try:
        r = core.stop(job)
        job.finish(result={"uploaded": r["uploaded"], "deleted": r["deleted"], "iid": r["iid"]})
    except VastgameError as e:
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
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(f, fcntl.LOCK_UN)
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
    fresh = d.get("pid") is None and time.time() - d.get("started", 0) < START_GRACE
    if not d["done"] and not alive and not fresh:
        d.update(done=True, ended=time.time(),
                 error={"key": "progress.lost", "params": {}, "text": "фоновый процесс завершился"})
        save(d)
    d["active"] = not d["done"]
    return d


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


def spawn(kind, **args):
    """Запустить операцию в фоновом процессе, который переживёт закрытие окна. False — уже идёт другая."""
    if running() or (state() or {}).get("active"):
        return False
    FileJob(kind)            # сразу «началось» в job.json — окно не покажет результат прошлой операции
    _launch(kind, args)
    return True


def _launch(kind, args):
    """Запустить фоновый процесс (тесты подменяют его потоком с work)."""
    with open(LOG, "a") as log:
        subprocess.Popen([sys.executable, str(HERE / "jobs.py"), kind, json.dumps(args)], cwd=HERE,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)   # своя сессия: закрытие окна его не завершит


def work(kind, args, log=lambda text: None):
    """Сама операция: взять блокировку (держим её до конца), выполнить, прислать уведомление.
    log — куда писать начало и конец (фоновый процесс пишет в job.log)."""
    with open(LOCK, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False                          # другая операция уже идёт — ничего не трогаем
        log(f"[{time.strftime('%F %T')}] {kind} pid={os.getpid()}")
        job = FileJob(kind, pid=os.getpid())
        RUNNERS[kind](job, **args)
        log(f"[{time.strftime('%F %T')}] {kind} done, error={(job.error or {}).get('key')}")
        notify.job_finished(kind, job.result, job.error, args.get("lang"))
    return True


def main():
    """Точка входа фонового процесса: python jobs.py play|stop '<json с параметрами>'."""
    if not work(sys.argv[1], json.loads(sys.argv[2]), log=lambda text: print(text, flush=True)):
        sys.exit("другая операция уже идёт")


if __name__ == "__main__":
    main()

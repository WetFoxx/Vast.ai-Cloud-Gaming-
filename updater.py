# updater.py — обновление vastgame по воздуху. Пока — проверка: есть ли на GitHub версия новее
# (скачивание, проверка подписи и замена себя — следующим шагом).
#
# Выпуски лежат в GitHub Releases публичного репозитория проекта (метка vastgame-v<версия>). К каждому
# прикладывается файл-описание vastgame-update.json: {"version": "0.2.1", ...}. Приложение скачивает только
# его — по постоянной ссылке «последний выпуск»: у неё нет лимита запросов, как у API GitHub (через VPN
# один адрес делят многие), — и сравнивает номер со своим. Нет интернета, выпусков, файл кривой —
# «обновлений нет», молча: проверка никогда не мешает работе.
# В этом репозитории раздел Releases — только для приложения, иначе «последний выпуск» укажет не туда.

import json
import os
import re
import threading
import time

import requests

from version import VERSION

REPO = "WetFoxx/Vast.ai-Cloud-Gaming-"
MANIFEST = "vastgame-update.json"
# Проверка на своём Mac без публикации: VASTGAME_UPDATE_URL=http://127.0.0.1:<порт>/vastgame-update.json
URL = os.environ.get("VASTGAME_UPDATE_URL") or f"https://github.com/{REPO}/releases/latest/download/{MANIFEST}"
CHECK_EVERY = 6 * 3600      # с: как часто проверять, пока окно открыто
RETRY_AFTER = 30 * 60       # с: узнать не удалось (нет сети) — снова через полчаса
TIMEOUT = 15                # с на ответ GitHub
MAX_BYTES = 64 * 1024       # файл-описание — сотни байт; больше — это не он


def parse(version):
    """«0.2.1» → (0, 2, 1); не номер версии — None."""
    if isinstance(version, str) and re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", version):
        return tuple(int(x) for x in version.split("."))
    return None


def newer(version, than=None):
    """version новее than (по умолчанию — своей версии)."""
    a, b = parse(version), parse(than or VERSION)
    return bool(a and b and a > b)


def page(version):
    """Страница выпуска на GitHub. Строится по номеру, а не берётся из файла-описания: поддельный файл
    не откроет чужую страницу."""
    return f"https://github.com/{REPO}/releases/tag/vastgame-v{version}"


def latest(url=None):
    """Номер последней версии на GitHub («0.2.1») или None — если узнать не удалось."""
    try:
        with requests.get(url or URL, timeout=TIMEOUT, stream=True, headers={"User-Agent": "vastgame"}) as r:
            if r.status_code != 200:
                return None
            body = b""
            for chunk in r.iter_content(8192):
                body += chunk
                if len(body) > MAX_BYTES:
                    return None
        data = json.loads(body)
    except (requests.RequestException, ValueError):
        return None
    v = data.get("version") if isinstance(data, dict) else None
    return v if parse(v) else None


class Checker:
    """Проверка для окна: status() можно спрашивать сколько угодно часто — сеть трогается в фоне, при первом
    вопросе и потом раз в CHECK_EVERY (не удалось — через RETRY_AFTER). Окно никогда не ждёт сети."""

    def __init__(self, fetch=latest, clock=time.time, spawn=None):
        self._fetch, self._clock = fetch, clock
        self._spawn = spawn or (lambda f: threading.Thread(target=f, daemon=True).start())
        self._lock = threading.Lock()
        self._known = None          # последняя версия на GitHub, какую удалось узнать
        self._next = 0.0            # когда проверять снова
        self._running = False

    def status(self):
        """{"current": своя версия, "available": есть новее, "latest": она, "page": её страница}."""
        with self._lock:
            start = not self._running and self._clock() >= self._next
            if start:
                self._running = True
            known = self._known
        if start:
            self._spawn(self._check)
            with self._lock:
                known = self._known     # проверка могла уже закончиться (в тестах она не в фоне)
        up = newer(known)
        return {"current": VERSION, "available": up, "latest": known if up else None,
                "page": page(known) if up else None}

    def _check(self):
        v = None
        try:
            v = self._fetch()
        finally:
            with self._lock:
                if v:
                    self._known = v
                self._next = self._clock() + (CHECK_EVERY if v else RETRY_AFTER)
                self._running = False

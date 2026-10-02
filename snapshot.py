"""[0.4.5] Последний удачный ответ медленных экранов (список машин, «Настройка», выбор игр, «Игры в облаке», «История»):
окно показывает его сразу с пометкой «обновляется…», свежий приходит следом (Vast, Drive, Steam отвечают секунды).
Файл snapshots.json рядом с settings.json (в тестах — во временной папке вместе с ним). Секретов здесь нет: то же,
что окно и так показывает на экране."""
import json
import os
import re
import threading

import settings

NAME = re.compile(r"(offers:(docker|kvm)|setup|sync|games|history:(7|30|90):(all|game))$")
_lock = threading.Lock()


def _file():
    return settings.FILE.with_name("snapshots.json")


def _load():
    try:
        d = json.loads(_file().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(name, data):
    """Запомнить ответ экрана (только удачный — с ошибкой не вызывать). Не записалось — не беда: это ускорение."""
    if not NAME.match(name) or not isinstance(data, dict) or data.get("error"):
        return
    with _lock:
        d = _load()
        d[name] = data
        tmp = _file().with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(d, ensure_ascii=False))
            os.replace(tmp, _file())                 # целиком или никак: окно в другом потоке читает этот же файл
        except (OSError, TypeError, ValueError):
            pass


def get(name):
    if not isinstance(name, str) or not NAME.match(name):
        return None
    with _lock:
        return _load().get(name)

# steamnames.py — названия игр Steam по номеру (appid): публичный API магазина Steam, без ключа.
# Ответы запоминаются в папке настроек (steam_names.json), чтобы не спрашивать Steam каждый раз.

import json

import requests

from settings import config_dir

CACHE = config_dir() / "steam_names.json"


def _load():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def names(appids):
    """{appid: название} для тех номеров, которые Steam знает. Неизвестные тоже запоминаются (как ""),
    чтобы не спрашивать о них снова; в ответ они не попадают."""
    cache = _load()
    new = False
    for appid in {str(a) for a in appids} - set(cache):
        try:
            r = requests.get("https://store.steampowered.com/api/appdetails",
                             params={"appids": appid, "filters": "basic"}, timeout=10)
            d = r.json().get(appid) or {}
            cache[appid] = d["data"]["name"] if d.get("success") else ""
            new = True
        except Exception:
            pass   # нет сети — спросим в следующий раз
    if new:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    return {a: cache[a] for a in map(str, appids) if cache.get(a)}


def for_archives(archive_names):
    """{appid: название} для архивов сохранений pfx--<appid> из списка имён."""
    return names([n[5:] for n in archive_names if n.startswith("pfx--")])

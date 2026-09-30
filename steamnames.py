# steamnames.py — названия игр Steam по номеру (appid): публичный API магазина Steam, без ключа.
# Ответы запоминаются в папке настроек (steam_names.json), чтобы не спрашивать Steam каждый раз.

import json
import re

import requests

from settings import config_dir

CACHE = config_dir() / "steam_names.json"
COVERS = config_dir() / "steam_covers.json"   # {appid: адрес маленькой обложки} — для списка игр на экране выбора
COVER_HOST = re.compile(r"^https://[a-z0-9.-]+\.steamstatic\.com/")


def _load(path=None):
    try:
        return json.loads((path or CACHE).read_text())
    except (OSError, ValueError):
        return {}


def _fetch(appid):
    """(название, обложка) из магазина Steam; игры нет — ("", ""); нет сети — None. Адрес обложки — из ответа:
    у новых игр в нём хеш, по номеру его не построить."""
    try:
        r = requests.get("https://store.steampowered.com/api/appdetails",
                         params={"appids": appid, "filters": "basic"}, timeout=10)
        d = r.json().get(appid) or {}
    except Exception:
        return None
    if not d.get("success"):
        return "", ""
    cover = (d.get("data") or {}).get("capsule_image") or ""
    return d["data"].get("name") or "", cover if COVER_HOST.match(cover) else ""


def _ask(appids, need):
    """Спросить Steam о номерах, которых нет в нужном кэше (need — CACHE или COVERS); запомнить оба ответа."""
    cache, covers = _load(CACHE), _load(COVERS)
    todo = {str(a) for a in appids} - set(cache if need is CACHE else covers)
    changed = False
    for appid in todo:
        got = _fetch(appid)
        if got is None:
            continue                      # нет сети — спросим в следующий раз
        cache[appid], covers[appid] = got
        changed = True
    if changed:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        COVERS.write_text(json.dumps(covers, ensure_ascii=False, indent=1))
    return cache, covers


def names(appids):
    """{appid: название} для тех номеров, которые Steam знает. Неизвестные тоже запоминаются (как ""),
    чтобы не спрашивать о них снова; в ответ они не попадают."""
    cache, _ = _ask(appids, CACHE)
    return {a: cache[a] for a in map(str, appids) if cache.get(a)}


def covers(appids):
    """{appid: адрес обложки} — маленькая картинка игры из магазина Steam (231x87)."""
    _, cov = _ask(appids, COVERS)
    return {a: cov[a] for a in map(str, appids) if cov.get(a)}


def for_archives(archive_names):
    """{appid: название} для архивов сохранений pfx--<appid> из списка имён."""
    return names([n[5:] for n in archive_names if n.startswith("pfx--")])

# cloudgames.py — игры и сохранения в облаке (Google Drive): список и удаление в корзину.
# Пользуются games.py (терминал) и окно приложения (gui.py).
#
# Если машина сейчас работает, удаление идёт через `wolf forget` на ней: wolf запоминает, что
# архив убран, и не выгружает игру заново. Без машины — напрямую через rclone в корзину Drive.


import re

import drive
import settings
import steamnames
from vastapi import VastgameError, list_instances
import wolfnode
from wolfnode import ssh_run, ts_node

KINDS = ("sgame", "game", "pfx")   # игра Steam, игра не из Steam, сохранения (префикс Proton)


def listing():
    """{"games": [{name, kind, title, appid, bytes, mtime, files}], "service": {"names", "bytes"}}.

    title — имя папки игры или, для сохранений, название игры из Steam (если Steam её знает).
    Служебные архивы (identity, steam-state, …) — отдельно: их не удаляют."""
    if not drive.token():
        raise VastgameError("Нет токена Google Drive — пройди первую настройку: vastgame setup", key="games.no_token")
    arch = drive.archives()
    if arch is None and drive.ensure_folder():   # папки ещё нет (первый запуск) — создать и открыть пустую
        arch = drive.archives()
    if arch is None:
        raise VastgameError("Не удалось открыть Google Drive — проверь интернет или пройди шаг 2: vastgame setup",
                            key="games.drive_failed")
    names = steamnames.for_archives(arch)
    games, service = [], []
    for base, info in arch.items():
        kind = base.split("--")[0] if "--" in base else None
        if kind not in KINDS:
            service.append((base, info["size"]))
            continue
        rest = base.split("--", 1)[1]
        games.append({"name": base, "kind": kind, "appid": rest if kind == "pfx" else None,
                      "title": names.get(rest, "") if kind == "pfx" else rest,
                      "bytes": info["size"], "mtime": info["mtime"], "files": info["files"],
                      # служебное Steam (Proton, Runtime…) и его «сохранения» — нужно для запуска игр
                      "system": bool(TOOL.match(base)) or (kind == "pfx" and rest in TOOL_APPIDS)})
    games.sort(key=lambda g: (KINDS.index(g["kind"]), -g["bytes"]))
    return {"games": games,
            "service": {"names": sorted(n for n, _ in service), "bytes": sum(b for _, b in service)}}


# Служебное Steam — не игры: в списке для выбора их нет, синхронизирует машина сама (как wolf-setup.sh v4.6).
# Номера — их «сохранения» (Proton Experimental, Steam Linux Runtime, Steamworks…), пока нет списка игр с машины.
TOOL = re.compile(r"^sgame--(Proton|SteamLinuxRuntime|Steam Linux Runtime|Steamworks Shared$|Steam Controller Configs$)")
TOOL_APPIDS = {"1493710", "1391110", "1628350", "1070560", "228980", "2180100", "1826330", "1161040"}


def rows(data, index):
    """Одна строка на игру — для выбора синхронизации: [{"id", "title", "kind": steam|folder|saves,
    "game": архив файлов или None, "game_bytes", "saves": архив сохранений или None, "saves_bytes", "appid"}].
    data — listing(), index — drive.games_index() (архив → номер Steam и название; без него игра и её сохранения
    могут оказаться отдельными строками). Служебное скрыто. title "" — название неизвестно (окно покажет номер)."""
    by = {g["name"]: g for g in data["games"]}
    idx = {g["archive"]: g for g in index}
    tools = TOOL_APPIDS | {str(g.get("appid")) for g in index if g.get("tool")}
    out, used = [], set()
    for name, g in by.items():
        if g["kind"] not in ("sgame", "game") or TOOL.match(name) or (idx.get(name) or {}).get("tool"):
            continue
        meta = idx.get(name) or {}
        appid = str(meta.get("appid") or "")
        saves = f"pfx--{appid}" if appid and f"pfx--{appid}" in by else None
        used.add(saves)
        out.append({"id": name, "title": meta.get("name") or g["title"].replace("_", " "),
                    "kind": "steam" if g["kind"] == "sgame" else "folder", "appid": appid or None,
                    "game": name, "game_bytes": g["bytes"],
                    "saves": saves, "saves_bytes": by[saves]["bytes"] if saves else 0})
    names = {str(g.get("appid")): g.get("name") for g in index if g.get("appid")}
    for name, g in by.items():
        if g["kind"] != "pfx" or name in used or g["appid"] in tools:
            continue
        out.append({"id": name, "title": names.get(g["appid"]) or g["title"] or "", "kind": "saves",
                    "appid": g["appid"], "game": None, "game_bytes": 0, "saves": name, "saves_bytes": g["bytes"]})
    out.sort(key=lambda r: (-r["game_bytes"], (r["title"] or "~").lower()))
    return out


def selection(rows_):
    """Выбор синхронизации для строк rows(): {"games": {архив: bool}, "saves": {архив: bool}} — запомненный
    (settings "sync_sel"). Игры, которых там нет, — выкл., сохранения — вкл. Первый раз после обновления (выбора
    ещё нет, но есть прежние тумблеры «игры Steam / из папки Games / сохранения») — по этим тумблерам."""
    sel = settings.get("sync_sel")
    first = not isinstance(sel, dict)
    sel = {"games": dict((sel or {}).get("games") or {}), "saves": dict((sel or {}).get("saves") or {})}
    old = settings.get("sync") if first else None
    for r in rows_:
        if r["game"] and r["game"] not in sel["games"]:
            on = bool(old and old.get("steam_games" if r["kind"] == "steam" else "other_games"))
            if first:
                sel["games"][r["game"]] = on
        if r["saves"] and r["saves"] not in sel["saves"] and first and old and old.get("saves") is False:
            sel["saves"][r["saves"]] = False
    if first:
        settings.set("sync_sel", sel)
    return {"games": {r["game"]: bool(sel["games"].get(r["game"], False)) for r in rows_ if r["game"]},
            "saves": {r["saves"]: bool(sel["saves"].get(r["saves"], True)) for r in rows_ if r["saves"]}}


def remember(choice):
    """Запомнить выбор со страницы ({"games": {архив: bool}, "saves": {архив: bool}}) поверх прежнего; вернуть
    для аренды {"games": [архивы игр], "saves_off": [архивы сохранений]} — всё, что включено/выключено."""
    sel = settings.get("sync_sel")
    sel = {"games": dict((sel or {}).get("games") or {}), "saves": dict((sel or {}).get("saves") or {})}
    for part, prefix in (("games", ("sgame--", "game--")), ("saves", ("pfx--",))):
        for k, v in ((choice or {}).get(part) or {}).items():
            if isinstance(k, str) and k.startswith(prefix):
                sel[part][k] = bool(v)
    settings.set("sync_sel", sel)
    return {"games": sorted(k for k, v in sel["games"].items() if v),
            "saves_off": sorted(k for k, v in sel["saves"].items() if not v)}


def remember_answer(kind, archive, yes):
    """Ответ на вопрос машины — в выбор для следующих запусков: новая игра «да» — качать, «нет» — не качать;
    удалённая: «да» — её больше нет нигде, «нет» — осталась в облаке, но в следующий раз не качать (её удалили
    с машины — значит, сейчас она не нужна; включить можно в списке)."""
    sel = settings.get("sync_sel")
    sel = {"games": dict((sel or {}).get("games") or {}), "saves": dict((sel or {}).get("saves") or {})}
    if kind == "del" and yes:
        sel["games"].pop(archive, None)
    else:
        sel["games"][archive] = bool(kind == "new" and yes)
    settings.set("sync_sel", sel)


def find(query, games):
    """Архивы по полному имени или его части (или части названия игры)."""
    q = query.lower()
    exact = [g for g in games if g["name"].lower() == q]
    return exact or [g for g in games if q in g["name"].lower() or q in (g["title"] or "").lower()]


def remove(name, on_notice=None):
    """Убрать архив name (и его дельты) из облака в корзину Drive. Возвращает "machine" или "drive" —
    каким путём убрали. on_notice(строка) — сообщения Tailscale SSH (ссылка подтверждения входа)."""
    games = listing()["games"]
    g = next((x for x in games if x["name"] == name), None)
    if not g:
        raise VastgameError(f"В облаке нет игры или сохранений «{name}».", key="games.not_found", name=name)
    inst = list_instances()
    node = ts_node() if inst else None
    if inst and node:
        # Машина работает — через wolf: он пометит архив и не выгрузит его заново.
        # wolf forget всегда отвечает кодом 0 — результат проверяем по списку ниже
        code, out = wolfnode.run(node[1], "forget", [name], timeout=300, on_notice=on_notice, ssh=ssh_run)
        if code != 0:
            raise VastgameError(f"wolf forget не сработал (код {code}): {out.strip()[-300:]}",
                                key="games.forget_failed", code=code, out=out.strip()[-300:])
        how = "machine"
    elif inst:
        raise VastgameError("Машина арендована, но её узла нет в Tailscale — удалять мимо неё нельзя:\n"
                            "wolf на ней выгрузил бы игру заново. Дождись запуска (vastgame info) или останови её.",
                            key="games.no_node")
    else:
        if not drive.trash(g["files"]):
            raise VastgameError("Не все файлы удалось убрать — запусти команду ещё раз.", key="games.trash_failed")
        how = "drive"
    # Проверка: в облаке архива больше нет
    if name in (drive.archives() or {}):
        raise VastgameError(f"!!! {name} всё ещё в облаке — проверь на Drive.", key="games.still_there", name=name)
    return how

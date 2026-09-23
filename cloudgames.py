# cloudgames.py — игры и сохранения в облаке (Google Drive): список и удаление в корзину.
# Пользуются games.py (терминал) и окно приложения (gui.py).
#
# Если машина сейчас работает, удаление идёт через `wolf forget` на ней: wolf запоминает, что
# архив убран, и не выгружает игру заново. Без машины — напрямую через rclone в корзину Drive.

import shlex   # безопасно подставить имя с пробелами в команду для машины

import drive
import steamnames
from vastapi import VastgameError, list_instances
from wolfnode import ssh_run, ts_node

KINDS = ("sgame", "game", "pfx")   # игра Steam, игра не из Steam, сохранения (префикс Proton)


def listing():
    """{"games": [{name, kind, title, appid, bytes, mtime, files}], "service": {"names", "bytes"}}.

    title — имя папки игры или, для сохранений, название игры из Steam (если Steam её знает).
    Служебные архивы (identity, steam-state, …) — отдельно: их не удаляют."""
    if not drive.token():
        raise VastgameError("Нет токена Google Drive — пройди первую настройку: vastgame setup", key="games.no_token")
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
                      "bytes": info["size"], "mtime": info["mtime"], "files": info["files"]})
    games.sort(key=lambda g: (KINDS.index(g["kind"]), -g["bytes"]))
    return {"games": games,
            "service": {"names": sorted(n for n, _ in service), "bytes": sum(b for _, b in service)}}


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
        code, out = ssh_run(node[1], f"wolf forget {shlex.quote(name)}", timeout=300, on_notice=on_notice)
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

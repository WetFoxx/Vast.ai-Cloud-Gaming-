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

KINDS = ("sgame", "game", "lgame", "pfx", "mod", "prog")   # игра Steam, не из Steam, [v5.6] файлы игры её лаунчера,
                                             # сохранения (префикс Proton), моды (v5.0), [v5.4] программы (папка Programs)
MOD = re.compile(r"^mod--(\d+)--(.*)$")      # моды игры Steam: mod--НОМЕР--Название
LGAME = re.compile(r"^lgame--(\d+)--(.*)$")  # [v5.6] файлы игры, которые докачал её лаунчер: lgame--НОМЕР--Название
# [v5.7] данные игры (сохранения и настройки, которые игра пишет в свою папку): gsave--НОМЕР--Название. На экране —
# в строке сохранений той же игры (pfx--НОМЕР), не отдельным пунктом; «Убрать» убирает оба
GSAVE = re.compile(r"^gsave--(\d+)--(.*)$")


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
    games, service, gsaves = [], [], []
    for base, info in arch.items():
        if GSAVE.match(base):
            gsaves.append((base, info))
            continue
        kind = base.split("--")[0] if "--" in base else None
        if kind not in KINDS:
            service.append((base, info["size"]))
            continue
        rest = base.split("--", 1)[1]
        mod = MOD.match(base) or LGAME.match(base)
        if kind in ("mod", "lgame") and not mod:
            service.append((base, info["size"]))
            continue
        games.append({"name": base, "kind": kind, "appid": rest if kind == "pfx" else mod[1] if mod else None,
                      "title": names.get(rest, "") if kind == "pfx" else mod[2] if mod else rest,
                      "bytes": info["size"], "mtime": info["mtime"], "files": info["files"],
                      # служебное Steam (Proton, Runtime…) и его «сохранения» — нужно для запуска игр
                      "system": bool(TOOL.match(base)) or (kind == "pfx" and rest in TOOL_APPIDS)})
    for base, info in gsaves:
        m = GSAVE.match(base)
        g = next((x for x in games if x["kind"] == "pfx" and x["appid"] == m[1]), None)
        if g:
            g["also"] = g.get("also", []) + [base]
            g["bytes"] += info["size"]
            g["files"] = g["files"] + info["files"]
            g["mtime"] = max(g["mtime"] or "", info["mtime"] or "")
            g["title"] = g["title"] or m[2]
        else:
            games.append({"name": base, "kind": "pfx", "appid": m[1], "title": names.get(m[1], "") or m[2],
                          "bytes": info["size"], "mtime": info["mtime"], "files": info["files"], "system": False})
    games.sort(key=lambda g: (KINDS.index(g["kind"]), -g["bytes"]))
    return {"games": games,
            "service": {"names": sorted(n for n, _ in service), "bytes": sum(b for _, b in service)}}


# Служебное Steam — не игры: в списке для выбора их нет, синхронизирует машина сама (как wolf-setup.sh v4.6).
# Номера — их «сохранения» (Proton Experimental, Steam Linux Runtime, Steamworks…), пока нет списка игр с машины.
TOOL = re.compile(r"^sgame--(Proton|SteamLinuxRuntime|Steam Linux Runtime|Steamworks Shared$|Steam Controller Configs$)")
TOOL_APPIDS = {"1493710", "1391110", "1628350", "1070560", "228980", "2180100", "1826330", "1161040"}


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


def mods():
    """{номер игры: {"name", "bytes", "mtime"}} — моды в облаке (v5.0); Drive не открылся — None."""
    x = extras()
    return None if x is None else x["mods"]


def extras():
    """Одним списком Drive: {"mods": {номер: {name, bytes, mtime}}, "lgame": {то же для файлов игры лаунчера}} или
    None — Drive не открылся."""
    arch = drive.archives()
    if arch is None:
        return None
    out = {"mods": {}, "lgame": {}}
    for base, info in arch.items():
        for key, rx in (("mods", MOD), ("lgame", LGAME)):
            m = rx.match(base)
            if m:
                out[key][m[1]] = {"name": base, "bytes": info["size"], "mtime": info["mtime"]}
    return out


# [v5.6] Рецепты — готовые наборы модов для конкретных игр (машина: wolf-recipes.py); у игры — один переключатель
RECIPES = {"ff14-ru": ("312060", "39210")}       # русский перевод xivrus.ru: FINAL FANTASY XIV пробная и полная


def recipe_for(appid):
    """Рецепт, который есть у этой игры, или None."""
    return next((r for r, ids in RECIPES.items() if str(appid) in ids), None)


def remember_recipe(appid, on):
    sel = dict(settings.get("recipe_sel") or {})
    sel[str(appid)] = bool(on)
    settings.set("recipe_sel", sel)


def recipes_list():
    """{номер: [рецепт]} — включённые, машине (VG_SYNC "recipes")."""
    return {a: [recipe_for(a)] for a, v in sorted((settings.get("recipe_sel") or {}).items()) if v and recipe_for(a)}


def remember_gdata(appid, on):
    """[v5.6] Тумблер «Хранить игру в облаке» (игры, которые докачивает их лаунчер) — для следующих машин."""
    sel = dict(settings.get("gdata_sel") or {})
    sel[str(appid)] = bool(on)
    settings.set("gdata_sel", sel)


def gdata_list():
    """Номера игр с включённым «Хранить игру в облаке» — машине (VG_SYNC "gdata")."""
    return sorted(a for a, v in (settings.get("gdata_sel") or {}).items() if v and str(a).isdigit())


def mods_sel():
    """[v5.7] Выбор «С модами / Без модов» по играм {номер: bool}. Один раз стирает «выключено» прежних версий: там
    тумблер «Моды» был выключен по умолчанию и записывался при каждом запуске игры — это не выбор человека, а теперь по
    умолчанию «С модами» (решение Алексея 2026-10-14)."""
    sel = dict(settings.get("mods_sel") or {})
    if not settings.get("mods_sel_v57"):
        sel = {a: v for a, v in sel.items() if v}
        settings.set("mods_sel", sel)
        settings.set("mods_sel_v57", True)
    return sel


def remember_mods(appid, yes):
    """Выбор «С модами / Без модов» этой игры — для следующих машин (VG_SYNC "mods")."""
    sel = mods_sel()
    sel[str(appid)] = bool(yes)
    settings.set("mods_sel", sel)


def mods_answers():
    """{"yes": [номера], "no": [номера], "all": True} — игры «С модами» / «Без модов», для машины (v5.1: вопросов нет;
    «Без модов» — моды остаются в облаке, изменения не сохраняются)."""
    sel = mods_sel()
    # [v5.7] «С модами» — по умолчанию у всех игр ("all"), «Без модов» — исключения ("no")
    return {"yes": sorted(a for a, v in sel.items() if v and str(a).isdigit()),
            "no": sorted(a for a, v in sel.items() if not v and str(a).isdigit()), "all": True}


def remember_answer(kind, archive, yes):
    """Ответ на вопрос машины — в выбор для следующих запусков: новая игра «да» — качать, «нет» — не качать;
    удалённая: «да» — её больше нет нигде, «нет» — осталась в облаке, но в следующий раз не качать (её удалили
    с машины — значит, сейчас она не нужна; включить можно в списке)."""
    if kind == "mod":
        remember_mods(archive.split("--", 1)[1], yes)
        return
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
        for n in [name] + g.get("also", []):              # [v5.7] со строкой — данные игры (страница: по одному)
            code, out = wolfnode.run(node[1], "forget", [n], timeout=300, on_notice=on_notice, ssh=ssh_run)
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
    left = drive.archives() or {}
    for n in [name] + g.get("also", []):
        if n in left:
            raise VastgameError(f"!!! {n} всё ещё в облаке — проверь на Drive.", key="games.still_there", name=n)
    return how

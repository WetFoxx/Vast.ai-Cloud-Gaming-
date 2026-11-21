# history.py — история сессий и траты.
#
# Два источника:
#  * реальные списания Vast по каждой машине (GET api/v0/charges/, format=tree): часы GPU, диск,
#    трафик, сумма — работает и для машин, арендованных через сайт;
#  * своя запись сессий на этом компьютере (sessions.json в папке настроек): когда начата и закончена,
#    видеокарта, страна, цена часа — подробности, которых нет в счетах.

import hashlib
import json
import os
import re
import time
from collections import defaultdict

from settings import config_dir
from vastapi import HOST, call, read_key

FILE = config_dir() / "sessions.json"
HOURS = re.compile(r"([\d.]+)\s*hours")


# ------------------------------------------------------------------ своя запись сессий ---

def _load():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return []


def _save(rows):
    FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1))


def started(iid, offer, t0=None, game=None):
    """Запомнить начало сессии: машина арендована. game — игра, с которой арендовали ({"appid", "name"}), для истории."""
    rows = _load()
    row = {"iid": iid, "start": int(t0 or time.time()), "end": None,
           "gpu": offer.get("gpu_name"), "country": (offer.get("geolocation") or "").strip(", "),
           "price": offer.get("dph_total"), "machine_id": offer.get("machine_id")}
    if game and game.get("appid"):
        row["game"] = {"appid": str(game["appid"]), "name": game.get("name") or ""}
    rows.append(row)
    _save(rows)


def ended(iid, when=None):
    """Запомнить конец сессии: машина удалена."""
    rows = _load()
    for r in rows:
        if r["iid"] == iid and not r["end"]:
            r["end"] = int(when or time.time())
    _save(rows)


def reconcile(running_ids):
    """Закрыть сессии машин, которых больше нет (удалили на сайте Vast или окно было закрыто)."""
    rows, changed = _load(), False
    for r in rows:
        if not r["end"] and r["iid"] not in running_ids:
            r["end"], r["closed_outside"], changed = int(time.time()), True, True
    if changed:
        _save(rows)


LAST_MIN_SEC = 300   # «последняя машина» — та, что проработала хоть 5 минут (не проигравшая забег)


def last_machine(skip=()):
    """Последняя сессия с известной машиной хоста (machine_id), кроме машин из skip; или None.
    Машины, удалённые через секунды (проиграли забег, не запустились), не считаются."""
    def played(r):
        return not r.get("end") or r["end"] - r["start"] >= LAST_MIN_SEC
    return next((r for r in reversed(_load())
                 if r.get("machine_id") and str(r["machine_id"]) not in skip and played(r)), None)


def current(iid):
    """Открытая сессия этой машины (для «идёт N мин, ~$X») или None."""
    return next((r for r in reversed(_load()) if r["iid"] == iid and not r["end"]), None)


# ------------------------------------------------------------------ реальные траты Vast ---

SETTLED = 2 * 86400   # [0.4.5] сутки старше двух дней Vast уже не меняет — их счета берём из своей памяти


def _fetch(gte, lte):
    """Записи счетов Vast (по одной на машину за сутки UTC, start — начало суток) за дни gte..lte — все страницы."""
    params = {"select_filters": json.dumps({"day": {"gte": gte, "lte": lte}}), "limit": 100, "format": "tree"}
    rows = []
    while True:
        r = call("GET", "api/v0/charges/", params=params, base=HOST)
        rows += [_slim(x) for x in r.get("results", [])]
        if not r.get("next_token"):
            return rows
        params["after_token"] = r["next_token"]


def _slim(x):
    """Запись счёта — только то, что считает charges() (Vast кладёт в неё много лишнего: память была 365 КБ)."""
    return {"type": x.get("type"), "source": x.get("source"), "start": x.get("start"), "amount": x.get("amount"),
            "metadata": {"label": (x.get("metadata") or {}).get("label")},
            "items": [{"type": y.get("type"), "amount": y.get("amount"),
                       **({"description": y.get("description")} if y.get("type") == "gpu" else {})}
                      for y in x.get("items") or []]}


def _charges_file():
    return FILE.with_name("charges.json")      # рядом с sessions.json (в тестах — во временной папке)


def _account():
    """Чей ключ Vast (отпечаток, не сам ключ): память счетов другого аккаунта не годится."""
    return hashlib.sha256(read_key().encode()).hexdigest()[:16]


def _raw(days):
    """Записи счетов за days дней. [0.4.5] Окончательные сутки (старше SETTLED) — из своей памяти charges.json,
    из Vast — только последние: «История» за 30 дней была 5 страниц по ~1.7 с подряд."""
    now = int(time.time())
    lo, cut = now - days * 86400, (now // 86400) * 86400 - SETTLED
    acc = _account()
    try:
        c = json.loads(_charges_file().read_text())
        ok = c["account"] == acc and c["from"] <= lo and isinstance(c["items"], list)
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    if ok:
        new = _fetch(c["to"], now)
        rows = [x for x in c["items"] if x.get("start", 0) >= lo] + new
        c = {**c, "to": max(c["to"], cut), "items": c["items"] + [x for x in new if x.get("start", 0) < cut]}
    else:
        rows = _fetch(lo, now)
        c = {"account": acc, "from": lo, "to": cut, "items": [x for x in rows if x.get("start", 0) < cut]}
    try:
        tmp = _charges_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(c))
        os.replace(tmp, _charges_file())
    except OSError:
        pass
    return rows


def charges(days):
    """Списания Vast по машинам за последние days дней:
    {iid: {"amount", "gpu", "disk", "traffic", "hours", "first", "last"}} (first/last — дни, unix)."""
    out = defaultdict(lambda: {"amount": 0.0, "gpu": 0.0, "disk": 0.0, "traffic": 0.0, "hours": 0.0,
                               "first": None, "last": None, "label": None})
    for x in _raw(days):
        m = re.match(r"instance-(\d+)$", x.get("source") or "")
        if x.get("type") != "instance" or not m:
            continue
        c = out[int(m.group(1))]
        c["amount"] += x.get("amount") or 0
        c["label"] = c["label"] or (x.get("metadata") or {}).get("label")
        c["first"] = min(filter(None, [c["first"], x.get("start")]))
        c["last"] = max(filter(None, [c["last"], x.get("start")]))
        for y in x.get("items") or []:
            kind = {"gpu": "gpu", "disk": "disk", "bwd": "traffic", "bwu": "traffic"}.get(y.get("type"))
            if kind:
                c[kind] += y.get("amount") or 0
            if y.get("type") == "gpu":
                h = HOURS.search(y.get("description") or "")
                c["hours"] += float(h.group(1)) if h else 0
    return dict(out)


def summary(days=30, all_machines=False):
    """История за days дней: сессии (свежие сверху) и итоги.

    Игровые — машины с меткой vastgame (её ставит аренда из приложения) или записанные в своей истории
    сессий. Остальные машины аккаунта (например, для других задач на Vast) по умолчанию не смешиваются
    с играми: они считаются отдельно в "other", а all_machines=True включает их в список и итоги."""
    ch = charges(days)
    local = {r["iid"]: r for r in _load()}
    sessions, other = [], {"count": 0, "amount": 0.0}
    for iid, c in ch.items():
        if c["amount"] < 0.0005:
            continue   # машина не запустилась — ничего не стоила, в истории не нужна
        lbl = c["label"] or ""
        if not all_machines and lbl != "vastgame" and not lbl.startswith("vastgame:") and iid not in local:
            other["count"] += 1
            other["amount"] += c["amount"]
            continue
        s = local.get(iid, {})
        sessions.append({"iid": iid, "start": s.get("start") or c["first"], "end": s.get("end"),
                         "gpu": s.get("gpu"), "country": s.get("country"), "price": s.get("price"), "game": s.get("game"),
                         "hours": round(c["hours"], 2), "amount": round(c["amount"], 3),
                         "gpu_cost": round(c["gpu"], 3), "disk_cost": round(c["disk"], 3),
                         "traffic_cost": round(c["traffic"], 3)})
    sessions.sort(key=lambda x: x["start"] or 0, reverse=True)
    total = sum(x["amount"] for x in sessions)
    hours = sum(x["hours"] for x in sessions)
    first = min((x["start"] for x in sessions if x["start"]), default=None)
    other["amount"] = round(other["amount"], 2)
    return {"days": days, "all": all_machines, "other": other, "sessions": sessions, "first": first,
            "now": int(time.time()),
            "totals": {"amount": round(total, 2), "hours": round(hours, 1), "count": len(sessions),
                       "per_hour": round(total / hours, 3) if hours else None,
                       "gpu": round(sum(x["gpu_cost"] for x in sessions), 2),
                       "disk": round(sum(x["disk_cost"] for x in sessions), 2),
                       "traffic": round(sum(x["traffic_cost"] for x in sessions), 3)}}

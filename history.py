# history.py — история сессий и траты.
#
# Два источника:
#  * реальные списания Vast по каждой машине (GET api/v0/charges/, format=tree): часы GPU, диск,
#    трафик, сумма — работает и для машин, арендованных через сайт;
#  * своя запись сессий на этом компьютере (sessions.json в папке настроек): когда начата и закончена,
#    видеокарта, страна, цена часа — подробности, которых нет в счетах.

import json
import re
import time
from collections import defaultdict

from settings import config_dir
from vastapi import HOST, call

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


def started(iid, offer, t0=None):
    """Запомнить начало сессии: машина арендована."""
    rows = _load()
    rows.append({"iid": iid, "start": int(t0 or time.time()), "end": None,
                 "gpu": offer.get("gpu_name"), "country": (offer.get("geolocation") or "").strip(", "),
                 "price": offer.get("dph_total"), "machine_id": offer.get("machine_id")})
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


def current(iid):
    """Открытая сессия этой машины (для «идёт N мин, ~$X») или None."""
    return next((r for r in reversed(_load()) if r["iid"] == iid and not r["end"]), None)


# ------------------------------------------------------------------ реальные траты Vast ---

def charges(days):
    """Списания Vast по машинам за последние days дней:
    {iid: {"amount", "gpu", "disk", "traffic", "hours", "first", "last"}} (first/last — дни, unix)."""
    now = int(time.time())
    params = {"select_filters": json.dumps({"day": {"gte": now - days * 86400, "lte": now}}),
              "limit": 100, "format": "tree"}
    out = defaultdict(lambda: {"amount": 0.0, "gpu": 0.0, "disk": 0.0, "traffic": 0.0, "hours": 0.0,
                               "first": None, "last": None})
    while True:
        r = call("GET", "api/v0/charges/", params=params, base=HOST)
        for x in r.get("results", []):
            m = re.match(r"instance-(\d+)$", x.get("source") or "")
            if x.get("type") != "instance" or not m:
                continue
            c = out[int(m.group(1))]
            c["amount"] += x.get("amount") or 0
            c["first"] = min(filter(None, [c["first"], x.get("start")]))
            c["last"] = max(filter(None, [c["last"], x.get("start")]))
            for y in x.get("items") or []:
                kind = {"gpu": "gpu", "disk": "disk", "bwd": "traffic", "bwu": "traffic"}.get(y.get("type"))
                if kind:
                    c[kind] += y.get("amount") or 0
                if y.get("type") == "gpu":
                    h = HOURS.search(y.get("description") or "")
                    c["hours"] += float(h.group(1)) if h else 0
        if not r.get("next_token"):
            return dict(out)
        params["after_token"] = r["next_token"]


def summary(days=30):
    """История за days дней: сессии (свежие сверху) и итоги."""
    ch = charges(days)
    local = {r["iid"]: r for r in _load()}
    sessions = []
    for iid, c in ch.items():
        if c["amount"] < 0.0005:
            continue   # машина не запустилась — ничего не стоила, в истории не нужна
        s = local.get(iid, {})
        sessions.append({"iid": iid, "start": s.get("start") or c["first"], "end": s.get("end"),
                         "gpu": s.get("gpu"), "country": s.get("country"), "price": s.get("price"),
                         "hours": round(c["hours"], 2), "amount": round(c["amount"], 3),
                         "gpu_cost": round(c["gpu"], 3), "disk_cost": round(c["disk"], 3),
                         "traffic_cost": round(c["traffic"], 3)})
    sessions.sort(key=lambda x: x["start"] or 0, reverse=True)
    total = sum(x["amount"] for x in sessions)
    hours = sum(x["hours"] for x in sessions)
    return {"days": days, "sessions": sessions,
            "totals": {"amount": round(total, 2), "hours": round(hours, 1), "count": len(sessions),
                       "per_hour": round(total / hours, 3) if hours else None,
                       "gpu": round(sum(x["gpu_cost"] for x in sessions), 2),
                       "disk": round(sum(x["disk_cost"] for x in sessions), 2),
                       "traffic": round(sum(x["traffic_cost"] for x in sessions), 3)}}

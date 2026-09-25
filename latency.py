# latency.py — настоящая задержка до машины, измеренная через Tailscale, и память о ней.
#
# `tailscale ping` идёт тем же путём, что и трансляция Moonlight (WireGuard через Tailscale, со всеми
# VPN на пути), поэтому это то, что человек почувствует в игре. Меряем, когда машина готова, и
# запоминаем для этой машины хоста (machine_id) и для её региона — в списке машин такие значения
# показываются вместо оценки по расстоянию (geo.py).

import json
import re
import statistics
import subprocess
import time

import paths
from settings import config_dir

FILE = config_dir() / "latency.json"
KEEP = 5                     # сколько последних замеров хранить на машину и регион (берём медиану)


def measure(ip, count=5):
    """Медиана задержки до узла Tailscale (мс) или None — не удалось измерить."""
    exe = paths.find("tailscale")
    if not exe:
        return None
    try:
        r = paths.run([exe, "ping", "--c", str(count), "--until-direct=false", "--timeout", "3s", ip],
                      count * 4 + 10, env=paths.tailscale_env())
    except (subprocess.TimeoutExpired, OSError):
        return None
    return parse(r.stdout)


def parse(out):
    """Медиана из строк «pong from … via … in 187ms» (бывает «in 1.2s») или None."""
    vals = []
    for num, unit in re.findall(r" in ([\d.]+)(ms|s)\b", out or ""):
        vals.append(float(num) * (1000 if unit == "s" else 1))
    return round(statistics.median(vals)) if vals else None


def _load():
    try:
        d = json.loads(FILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record(ms, machine_id=None, place=None):
    """Запомнить замер для машины хоста и её региона (geolocation Vast)."""
    if ms is None:
        return
    d = _load()
    for group, key in (("machines", machine_id), ("places", place)):
        if key in (None, ""):
            continue
        vals = d.setdefault(group, {}).setdefault(str(key), [])
        vals.append([int(time.time()), int(ms)])
        del vals[:-KEEP]
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(FILE)


def known():
    """{"machines": {id: мс}, "places": {регион: мс}} — медианы прошлых замеров."""
    d = _load()
    return {g: {k: round(statistics.median(v for _, v in rows)) for k, rows in (d.get(g) or {}).items() if rows}
            for g in ("machines", "places")}


def for_offer(o, remembered, me=None):
    """Задержка для строки списка: {"ms", "kind"}: measured — была на этой машине, region — на другой
    машине в этом регионе, estimate — оценка по расстоянию; ms None — неизвестно."""
    import geo
    m = remembered["machines"].get(str(o.get("machine_id")))
    if m is not None:
        return {"ms": m, "kind": "measured"}
    p = remembered["places"].get(o.get("geolocation") or "")
    if p is not None:
        return {"ms": p, "kind": "region"}
    return {"ms": geo.estimate(o.get("geolocation"), me), "kind": "estimate"}

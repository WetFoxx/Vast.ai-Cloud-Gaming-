# market.py — поиск подходящих машин на Vast и «чёрный список» хостов, которые подвели.
# Используют offers.py (таблица) и play.py (автовыбор машины).

import datetime
import json
import os
import re
import sys
from pathlib import Path

from vastapi import call

MIN_REL = 0.97        # надёжность машины не ниже (доля времени, когда хост был доступен)
STREAM_GB = 15        # трафик стрима, ГБ в час — для оценки стоимости трафика
MAX_TRAFFIC = 0.02    # машины с трафиком дороже, $ за ГБ, не показываем

# Игровые видеокарты: "RTX 4090", "GTX 1660 Ti", "RTX 4070S Ti".
# Не подходят профессиональные: "RTX A4000" (после RTX буква), "RTX 6000Ada" (Ada сразу после цифр).
GAMING = re.compile(r"^(RTX|GTX) \d{4}(?!Ada)")


def config_dir():
    """Папка настроек утилиты (не секретных): macOS — Application Support, Windows — %APPDATA%."""
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "vastgame"
    elif os.name == "nt":
        d = Path(os.environ.get("APPDATA", Path.home())) / "vastgame"
    else:
        d = Path.home() / ".config" / "vastgame"
    d.mkdir(parents=True, exist_ok=True)
    return d


BAD_HOSTS = config_dir() / "bad_hosts.json"   # {machine_id: {"reason": ..., "date": ...}}


def bad_hosts():
    try:
        return json.loads(BAD_HOSTS.read_text())
    except (OSError, ValueError):
        return {}


def mark_bad(machine_id, reason):
    """Запомнить хост, который не смог запустить машину, — больше его не предлагать."""
    if not machine_id:
        return
    bad = bad_hosts()
    bad[str(machine_id)] = {"reason": reason[:200], "date": datetime.date.today().isoformat()}
    BAD_HOSTS.write_text(json.dumps(bad, ensure_ascii=False, indent=1))


def search(disk, min_rel=MIN_REL, free=False, max_traffic=MAX_TRAFFIC, stream_gb=STREAM_GB):
    """Подходящие машины, от дешёвых к дорогим по полной цене часа.

    Возвращает список словарей-предложений Vast с добавленными полями:
    _traffic ($/ч за стрим), _total (аренда с диском + трафик).
    """
    # Фильтры поиска — проверены на живом API (см. CLAUDE.md, раздел Vast API)
    offers = call("POST", "bundles/", {
        "type": "ondemand",
        "rentable": {"eq": True},
        "num_gpus": {"eq": 1},
        "vms_enabled": {"eq": True},        # машина умеет запускать KVM-образы
        "compute_cap": {"gte": 750},        # как в шаблоне: видеокарты от GTX 16xx / RTX 20xx
        "reliability": {"gte": min_rel},
        "disk_space": {"gte": disk},        # на машине хватает места под наш диск
        "allocated_storage": disk,          # чтобы цена в час сразу включала этот диск
        "limit": 1000,
    }).get("offers", [])
    bad = bad_hosts()
    rows = []
    for o in offers:
        if not GAMING.match(o["gpu_name"]) or str(o.get("machine_id")) in bad:
            continue
        up = o.get("inet_up_cost") or 0       # $ за ГБ исходящего трафика (стрим идёт с машины к тебе)
        down = o.get("inet_down_cost") or 0   # $ за ГБ входящего (скачивание игр с Google Drive)
        # «Бесплатный» — дешевле $0.10 за ТБ: у таких хостов в API стоят копейки вроде 0.000003 $/ГБ
        if free and max(up, down) >= 0.0001:
            continue
        if max(up, down) > max_traffic:
            continue
        o["_traffic"] = up * stream_gb
        o["_total"] = o["dph_total"] + o["_traffic"]
        rows.append(o)
    return sorted(rows, key=lambda o: (o["_total"], -o["reliability"]))


def country(o):
    return (o.get("geolocation") or "?").strip(", ")


def print_table(rows, numbered=False):
    head = "  №" if numbered else ""
    print(f"{head}{'ID':>9}  {'GPU':<16} {'аренда':>7} {'трафик':>7} {'итого':>7}  "
          f"{'↑ $/ТБ':>7} {'↓ $/ТБ':>7}  {'надёжн':>6}  {'диск':>5}  страна")
    for n, o in enumerate(rows, 1):
        num = f"{n:>3}" if numbered else ""
        print(f"{num}{o['id']:>9}  {o['gpu_name']:<16} {o['dph_total']:>7.3f} {o['_traffic']:>7.3f} {o['_total']:>7.3f}  "
              f"{(o.get('inet_up_cost') or 0) * 1000:>7.2f} {(o.get('inet_down_cost') or 0) * 1000:>7.2f}  "
              f"{o['reliability']:>6.3f}  {o['disk_space']:>5.0f}  {country(o)}")

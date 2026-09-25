# market.py — поиск подходящих машин на Vast и «чёрный список» хостов, которые подвели.
# Используют offers.py (таблица) и play.py (автовыбор машины).

import datetime
import json
import re

import history
from settings import config_dir
from vastapi import call

MIN_REL = 0.97        # надёжность машины не ниже (доля времени, когда хост был доступен)
STREAM_GB = 15        # трафик стрима, ГБ в час — для оценки стоимости трафика
# Предела цены трафика нет (решение 2026-09-25): по реальным списаниям трафик почти не берётся ($0.019 за
# 30 дней при $15.19), а предел $0.02/ГБ отсекал 6 машин. В терминале можно задать: offers --max-traffic
MAX_TRAFFIC = None

# Игровые видеокарты: "RTX 4090", "GTX 1660 Ti", "RTX 4070S Ti".
# Не подходят профессиональные: "RTX A4000" (после RTX буква), "RTX 6000Ada" (Ada сразу после цифр).
GAMING = re.compile(r"^(RTX|GTX) \d{4}(?!Ada)")
# Docker (образ vastgame-desktop): Sunshine 2026 собрана с CUDA 13 — NVENC работает с драйвером хоста от 580
DOCKER_DRIVER = 580


# Хосты, которые не смогли запустить машину: {machine_id: {"reason": ..., "date": ...}}. Их НЕ прячем —
# сбой бывает временным, — а помечаем в списке; пометка снимается, когда машина на этом хосте запустилась.
BAD_HOSTS = config_dir() / "bad_hosts.json"
STARTS = config_dir() / "starts.json"     # за сколько секунд хосты доходили до running: {machine_id: [с, …]}


def bad_hosts():
    try:
        return json.loads(BAD_HOSTS.read_text())
    except (OSError, ValueError):
        return {}


def mark_bad(machine_id, reason):
    """Запомнить хост, который не смог запустить машину: в списке он будет с пометкой."""
    if not machine_id:
        return
    bad = bad_hosts()
    bad[str(machine_id)] = {"reason": reason[:200], "date": datetime.date.today().isoformat()}
    BAD_HOSTS.write_text(json.dumps(bad, ensure_ascii=False, indent=1))


def clear_bad(machine_id):
    """Машина на этом хосте запустилась — снять пометку."""
    bad = bad_hosts()
    if bad.pop(str(machine_id), None) is not None:
        BAD_HOSTS.write_text(json.dumps(bad, ensure_ascii=False, indent=1))



def start_key(machine_id, docker=False):
    """Ключ в starts.json: Docker-контейнер на том же хосте запускается иначе, чем KVM, — отдельно."""
    return f"d{machine_id}" if docker else str(machine_id)


def record_start(machine_id, seconds, docker=False):
    """Запомнить, за сколько хост дошёл до running (последние 5 раз) — в списке «запуск ~N мин»."""
    if machine_id is None:
        return
    try:
        d = json.loads(STARTS.read_text())
    except (OSError, ValueError):
        d = {}
    key = start_key(machine_id, docker)
    runs = (d.get(key) or [])[-4:] + [int(seconds)]
    d[key] = runs
    STARTS.write_text(json.dumps(d))


def start_minutes(machine_id, starts=None, docker=False):
    """Обычное время запуска хоста в минутах (медиана) или None — ещё не запускали."""
    if starts is None:
        try:
            starts = json.loads(STARTS.read_text())
        except (OSError, ValueError):
            starts = {}
    runs = sorted(starts.get(start_key(machine_id, docker)) or [])
    return max(1, round(runs[len(runs) // 2] / 60)) if runs else None

def driver_major(o):
    """Старший номер драйвера NVIDIA хоста ("580.173.02" → 580), 0 — неизвестен."""
    try:
        return int(str(o.get("driver_version") or "0").split(".")[0])
    except ValueError:
        return 0


def search(disk, min_rel=MIN_REL, free=False, max_traffic=MAX_TRAFFIC, stream_gb=STREAM_GB, where=None,
           docker=False):
    """Подходящие машины, от дешёвых к дорогим по полной цене часа.
    where — дополнительные фильтры Vast, например {"machine_id": {"eq": 140348}}.
    docker — для образа vastgame-desktop (обычный контейнер): KVM не нужен, нужен драйвер от DOCKER_DRIVER.

    Возвращает список словарей-предложений Vast с добавленными полями:
    _traffic ($/ч за стрим), _total (аренда с диском + трафик).
    """
    # Фильтры поиска — проверены на живом API (см. CLAUDE.md, раздел Vast API)
    offers = call("POST", "bundles/", {
        "type": "ondemand",
        "rentable": {"eq": True},
        "num_gpus": {"eq": 1},
        **({} if docker else {"vms_enabled": {"eq": True}}),   # KVM-образ — только там, где Vast разрешил VM
        "compute_cap": {"gte": 750},        # как в шаблоне: видеокарты от GTX 16xx / RTX 20xx
        "reliability": {"gte": min_rel},
        "disk_space": {"gte": disk},        # на машине хватает места под наш диск
        "allocated_storage": disk,          # чтобы цена в час сразу включала этот диск
        "limit": 1000,
        **(where or {}),
    }).get("offers", [])
    bad = bad_hosts()
    try:
        starts = json.loads(STARTS.read_text())
    except (OSError, ValueError):
        starts = {}
    rows = []
    for o in offers:
        if not GAMING.match(o["gpu_name"]):
            continue
        # Vast снял с хоста допуск к виртуальным машинам (у KVM на Vast часть хостов висит в Create/
        # Loading) — общая отметка verified тут ни при чём: у быстрой машины Алексея она «deverified»
        if o.get("is_vm_deverified") and not docker:
            continue
        if docker and driver_major(o) < DOCKER_DRIVER:
            continue
        o["_failed"] = (bad.get(str(o.get("machine_id"))) or {}).get("reason")   # раньше не запустилась
        o["_start_min"] = start_minutes(o.get("machine_id"), starts, docker)           # обычно запускается за N мин
        up = o.get("inet_up_cost") or 0       # $ за ГБ исходящего трафика (стрим идёт с машины к тебе)
        down = o.get("inet_down_cost") or 0   # $ за ГБ входящего (скачивание игр с Google Drive)
        # «Бесплатный» — дешевле $0.10 за ТБ: у таких хостов в API стоят копейки вроде 0.000003 $/ГБ
        if free and max(up, down) >= 0.0001:
            continue
        if max_traffic is not None and max(up, down) > max_traffic:
            continue
        o["_traffic"] = up * stream_gb
        o["_total"] = o["dph_total"] + o["_traffic"]
        rows.append(o)
    return sorted(rows, key=lambda o: (o["_total"], -o["reliability"]))


def last_machine(disk, docker=False):
    """Машина последней сессии (та же физическая машина хоста, диск у неё будет новый) и её предложение
    сейчас. None — сессий с известной машиной нет; "offer": None — машина занята или больше не сдаётся.
    Берётся последняя машина, которая в итоге запустилась (помеченные сбоем пропускаются);
    надёжность не проверяем — человек выбирает машину сам."""
    s = history.last_machine(skip=bad_hosts())
    if not s:
        return None
    rows = search(disk, min_rel=0, where={"machine_id": {"eq": s["machine_id"]}}, docker=docker)
    return {"machine_id": s["machine_id"], "gpu": s.get("gpu"), "country": s.get("country"),
            "start": s.get("start"), "offer": rows[0] if rows else None}


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
              f"{o['reliability']:>6.3f}  {o['disk_space']:>5.0f}  {country(o)}"
              + ("   ! раньше не запустилась" if o.get("_failed") else ""))

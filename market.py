# market.py — поиск подходящих машин на Vast и «чёрный список» хостов, которые подвели.
# Используют offers.py (таблица) и play.py (автовыбор машины).

import datetime
import json
import re

import history
import parallel
from settings import config_dir
from vastapi import call

MIN_REL = 0.97        # надёжность машины не ниже (доля времени, когда хост был доступен)
STREAM_GB = 15        # трафик стрима, ГБ в час — для оценки стоимости трафика
# Предела цены трафика нет (решение 2026-09-25): по реальным списаниям трафик почти не берётся ($0.019 за
# 30 дней при $15.19), а предел $0.02/ГБ отсекал 6 машин. В терминале можно задать: offers --max-traffic
MAX_TRAFFIC = None

# Игровые видеокарты: "RTX 4090", "GTX 1660 Ti", "RTX 4070S Ti".
GAMING = re.compile(r"^(RTX|GTX) \d{4}(?!Ada)")
# [0.3.1, решение 2026-10-01] Профессиональные с кодировщиком видео и выходами на монитор (Xorg в контейнере им
# нужен): RTX A2000–A6000, RTX PRO 4000–6000 (кроме серверной «S»), RTX 4000–6000 Ada, Quadro RTX, Titan RTX.
# Не подходят: без кодировщика (A100, H100, H200, B200) и майнинговые CMP; серверные без выходов (L4, L40, T4,
# A10, A40, RTX PRO 6000 S) — экран без монитора Sunshine не снимает (разбор 2026-09-27)
PRO = re.compile(r"^(RTX A(2000|4000|4500|5000|6000)|RTX PRO (4000|4500|5000|6000)( Max-Q| WS)?"
                 r"|RTX (4000|4500|5000|5880|6000) ?Ada|Q RTX (4000|5000|6000|8000)|Titan RTX)$")


def gpu_ok(name):
    """Видеокарта подходит для игр по трансляции: игровая или профессиональная с кодировщиком и выходами."""
    return bool(GAMING.match(name or "") or PRO.match(name or ""))
# Docker (образ vastgame-desktop): Sunshine 2026 на CUDA 13 — драйвер хоста от 580. В образе есть и Sunshine 2025.924
# на CUDA 12.9 для драйверов 535–575 (sunshine-start.sh выбирает сам), но порог не снижаем: живые проверки 2026-10-01
# (Quadro RTX 6000 / 535, RTX 3060 Ti / 570, RTX 3060 / 570) — на всех экран не поднимается, «Failed to acquire
# modesetting permission» (видеокартой владеет хост), до Sunshine не доходит. Такие хосты — в основном домашние.
DOCKER_DRIVER = 580
# Поколение видеокарты (compute capability ×100): от GTX 16xx / RTX 20xx. GTX 10xx (Pascal, 600) пробовали 2026-10-01:
# CUDA 13 (Sunshine 2026) их не поддерживает, а со старой Sunshine на CUDA 12.9 машина зависла при подключении
# Moonlight (кончились процессы) — отложено
MIN_COMPUTE = 750
PAIRS = True          # Docker: показывать машины, которые сдаются только парой видеокарт
# Страны, где машина не может работать: Google Drive (архивы) и GitHub (скрипт) заблокированы — в Китае
# машина висит на скачивании скрипта (живой случай 2026-09-26, GTX 1660 S)
BLOCKED = {"CN"}
# Диск медленнее — машину не показываем (решение 2026-09-27): игры с такого диска грузятся медленно, а восстановление
# из облака пишет каждый байт дважды (части во временную папку, потом распаковка). Живой случай 2026-09-26:
# Португалия, disk_bw 69 МБ/с (старый SATA SSD) — игра 10 ГБ восстанавливалась 20 МБ/с. У обычных машин
# 2700–3400 МБ/с; медленнее 200 — 8 из 340 в Docker и ни одной в KVM. Поля нет — скорость неизвестна, не прячем.
MIN_DISK_BW = 200     # МБ/с, поле Vast disk_bw
# Интернет медленнее — машину не показываем (решение 2026-10-15): с Google Drive качаются клиент Steam, сохранения и
# игры, с сайта NVIDIA — драйвер. Живой случай 2026-10-15: Гонконг, RTX 3060, inet_down 17 Мбит/с — драйвер 400 МБ
# 3 мин, клиент Steam 1.2 ГБ — 10 мин, «долгий Tailscale». Медленнее 100 — 10 из 240 машин в Docker, 0 из 7 в KVM
MIN_INET_DOWN = 100   # Мбит/с, поле Vast inet_down
# Памяти меньше — машину не показываем (решение 2026-10-01): в эти ГБ помещается всё сразу — рабочий стол, Sunshine,
# Steam с его браузером и игра, а подкачки в контейнере нет. Живой случай 2026-10-01: две машины RTX A2000 (Япония)
# с 7.7 ГБ перестали отвечать (Tailscale, страница статуса, Moonlight) ровно когда Steam начал качать игру. Порог —
# 15, а не 16: у 16-гиговых машин Vast показывает 15.0–15.6 ГБ (часть занимает хост). Ушло ~36 из 771 (Docker).
MIN_RAM_GB = 15       # ГБ, поле Vast cpu_ram (МБ)
# Docker: трафик контейнера Vast считает и берёт деньги (живой случай 2026-09-26, Таиланд: за час GPU $0.105,
# трафик $0.386 — стрим и скачивание игр). У KVM по реальным списаниям трафик не берётся ($0.000) — там не фильтруем.
# Цену трафика во вкладке Docker человек выбирает сам — фильтр «Трафик» на странице (по умолчанию до $1 за ТБ)


# Хосты, которые не смогли запустить машину: {machine_id: {"reason": ..., "date": ...}}. Их НЕ прячем —
# сбой бывает временным, — а помечаем в списке; пометка снимается, когда машина на этом хосте запустилась.
# Исключение — постоянные сбои (PERMANENT, ниже): такие хосты не показываются.
BAD_HOSTS = config_dir() / "bad_hosts.json"
STARTS = config_dir() / "starts.json"     # за сколько секунд хосты доходили до running: {machine_id: [с, …]}


def bad_hosts():
    try:
        return json.loads(BAD_HOSTS.read_text())
    except (OSError, ValueError):
        return {}


# [2026-10-15] Сбои, которые хост не исправит сам (повторялись на тех же хостах: 68380 Вьетнам, 152536 Вашингтон):
# экраном видеокарты владеет хост («Failed to acquire modesetting permission» → Xorg «no screens found») и у экрана
# нет выхода, который видит захват Sunshine («нет выхода на монитор»). Такие хосты не показываем HIDE_DAYS дней —
# потом снова с пометкой (хозяин мог что-то поменять). Остальные сбои бывают временными — только пометка
PERMANENT = ("no screens found", "нет выхода на монитор")
HIDE_DAYS = 30


def hidden(b):
    """Пометка хоста (из bad_hosts) — постоянный сбой не старше HIDE_DAYS дней: не показывать."""
    if not any(m in (b.get("reason") or "") for m in PERMANENT):
        return False
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(b.get("date") or "")).days < HIDE_DAYS
    except ValueError:
        return True


def mark_bad(machine_id, reason, key=None, **params):
    """Запомнить хост, который не смог запустить машину: в списке он будет с пометкой. reason — по-русски (терминал),
    key/params — та же причина для перевода в окне (без них подсказка в окне — общая фраза на языке окна)."""
    if not machine_id:
        return
    bad = bad_hosts()
    bad[str(machine_id)] = {"reason": reason[:200], "date": datetime.date.today().isoformat(),
                            **({"key": key, "params": params} if key else {})}
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
    query = {
        "type": "ondemand",
        "rentable": {"eq": True},
        "num_gpus": {"eq": 1},
        **({} if docker else {"vms_enabled": {"eq": True}}),   # KVM-образ — только там, где Vast разрешил VM
        "compute_cap": {"gte": MIN_COMPUTE},
        "reliability": {"gte": min_rel},
        "disk_space": {"gte": disk},        # на машине хватает места под наш диск
        "allocated_storage": disk,          # чтобы цена в час сразу включала этот диск
        "limit": 1000,
        **(where or {}),
    }
    # [0.3.1] Машины, которые сдаются только парой видеокарт (одной — нет): игра идёт на первой, вторая простаивает,
    # цена — за обе (~130 машин, 2026-10-01). Только Docker: в KVM две карты в виртуальной машине не проверялись.
    # [0.4.5] Оба поиска — одновременно
    pairs = docker and PAIRS
    found = parallel.run(lambda: call("POST", "bundles/", query).get("offers", []),
                         *([lambda: call("POST", "bundles/", {**query, "num_gpus": {"eq": 2}}).get("offers", [])]
                           if pairs else []))
    offers = found[0]
    if pairs:
        singles = {o.get("machine_id") for o in offers}
        for o in found[1]:
            if o.get("machine_id") not in singles:
                o["_gpus"] = 2
                offers.append(o)
    bad = bad_hosts()
    try:
        starts = json.loads(STARTS.read_text())
    except (OSError, ValueError):
        starts = {}
    rows = []
    for o in offers:
        if not gpu_ok(o["gpu_name"]):
            continue
        # Vast снял с хоста допуск к виртуальным машинам (у KVM на Vast часть хостов висит в Create/
        # Loading) — общая отметка verified тут ни при чём: у быстрой машины Алексея она «deverified»
        if (o.get("geolocation") or "").rsplit(",", 1)[-1].strip().upper() in BLOCKED:
            continue
        if o.get("is_vm_deverified") and not docker:
            continue
        if docker and driver_major(o) < DOCKER_DRIVER:     # драйвер 610+ — работает (проверено 2026-10-02, Испания)
            continue
        if (o.get("disk_bw") or MIN_DISK_BW) < MIN_DISK_BW:
            continue
        if (o.get("inet_down") or MIN_INET_DOWN) < MIN_INET_DOWN:
            continue
        if (o.get("cpu_ram") or MIN_RAM_GB * 1024) < MIN_RAM_GB * 1024:
            continue
        b = bad.get(str(o.get("machine_id"))) or {}
        if hidden(b):
            continue
        o["_failed"] = b.get("reason")                                     # раньше не запустилась
        o["_failed_why"] = {"key": b["key"], "params": b.get("params") or {}} if b.get("key") else None
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

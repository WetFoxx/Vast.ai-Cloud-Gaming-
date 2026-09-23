# offers.py — таблица машин Vast, подходящих для облачного игрового ПК.
# Запуск: .venv/bin/python offers.py            (диск 100 ГБ, надёжность от 0.98)
#         .venv/bin/python offers.py --disk 200 --free
# Поиск бесплатный: ничего не арендуется.

import argparse   # разбор параметров командной строки (--disk 200 и т.п.)
import re         # регулярные выражения — проверка названия видеокарты

from vastapi import call, my_template

# Параметры, которые можно поменять при запуске
p = argparse.ArgumentParser(description="Подходящие машины Vast для игр")
p.add_argument("--disk", type=int, help="размер диска, ГБ (по умолчанию — из шаблона, иначе 100)")
p.add_argument("--min-rel", type=float, default=0.98, help="минимальная надёжность (0.98)")
p.add_argument("--stream-gb", type=float, default=15, help="трафик стрима, ГБ в час (15)")
p.add_argument("--max-traffic", type=float, default=0.02, help="скрыть машины, где трафик дороже, $/ГБ (0.02)")
p.add_argument("--free", action="store_true", help="только машины с бесплатным трафиком (до $0.10 за ТБ)")
p.add_argument("--rows", type=int, default=20, help="сколько строк показать (20)")
a = p.parse_args()
if a.disk is None:
    tpl = my_template()   # размер диска по умолчанию задаётся в мастере и хранится в шаблоне
    a.disk = int((tpl or {}).get("recommended_disk_space") or 100)

# Фильтры поиска — проверены на живом API (см. CLAUDE.md, раздел Vast API)
query = {
    "type": "ondemand",
    "rentable": {"eq": True},
    "num_gpus": {"eq": 1},
    "vms_enabled": {"eq": True},        # машина умеет запускать KVM-образы
    "compute_cap": {"gte": 750},        # как в шаблоне: видеокарты от GTX 16xx / RTX 20xx
    "reliability": {"gte": a.min_rel},
    "disk_space": {"gte": a.disk},      # на машине хватает места под наш диск
    "allocated_storage": a.disk,        # чтобы цена в час сразу включала этот диск
    "limit": 1000,
}
offers = call("POST", "bundles/", query).get("offers", [])

# Игровые видеокарты: "RTX 4090", "GTX 1660 Ti", "RTX 4070S Ti".
# Не подходят профессиональные: "RTX A4000" (после RTX буква), "RTX 6000Ada" (Ada сразу после цифр).
GAMING = re.compile(r"^(RTX|GTX) \d{4}(?!Ada)")

rows = []
for o in offers:
    if not GAMING.match(o["gpu_name"]):
        continue
    up = o.get("inet_up_cost") or 0       # $ за ГБ исходящего трафика (стрим идёт с машины к тебе)
    down = o.get("inet_down_cost") or 0   # $ за ГБ входящего (скачивание игр с Google Drive)
    # «Бесплатный» — дешевле $0.10 за ТБ: у таких хостов в API стоят копейки вроде 0.000003 $/ГБ
    if a.free and max(up, down) >= 0.0001:
        continue
    if max(up, down) > a.max_traffic:
        continue
    traffic = up * a.stream_gb            # сколько стоит стрим за час
    rows.append((o["dph_total"] + traffic, traffic, up, down, o))

rows.sort(key=lambda r: r[0])   # сортировка по полной цене часа

if not rows:
    print("Подходящих машин нет — попробуй ослабить фильтры (--disk, --min-rel, --max-traffic)")
else:
    print(f"Диск {a.disk} ГБ · надёжность от {a.min_rel} · стрим {a.stream_gb:g} ГБ/ч"
          f" · найдено {len(rows)}, показано {min(len(rows), a.rows)}\n")
    print(f"{'ID':>9}  {'GPU':<16} {'аренда':>7} {'трафик':>7} {'итого':>7}  "
          f"{'↑ $/ТБ':>7} {'↓ $/ТБ':>7}  {'надёжн':>6}  {'диск':>5}  страна")
    for total, traffic, up, down, o in rows[:a.rows]:
        print(f"{o['id']:>9}  {o['gpu_name']:<16} {o['dph_total']:>7.3f} {traffic:>7.3f} {total:>7.3f}  "
              f"{up * 1000:>7.2f} {down * 1000:>7.2f}  {o['reliability']:>6.3f}  {o['disk_space']:>5.0f}  "
              f"{(o.get('geolocation') or '?').strip(', ')}")
    print("\nЦены в $ за час; аренда уже включает диск. Трафик = стрим × цена исходящего ГБ.")

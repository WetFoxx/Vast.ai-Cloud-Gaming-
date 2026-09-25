# offers.py — таблица машин Vast, подходящих для облачного игрового ПК.
# Запуск: vastgame offers            (диск — из шаблона, надёжность от 0.97, все страны, RTX/GTX)
#         vastgame offers --disk 200 --free
# Поиск бесплатный: ничего не арендуется. Сам поиск — в market.py (им же пользуется play).

import argparse   # разбор параметров командной строки (--disk 200 и т.п.)

import market
from vastapi import my_template

# Параметры, которые можно поменять при запуске
p = argparse.ArgumentParser(description="Подходящие машины Vast для игр")
p.add_argument("--disk", type=int, help="размер диска, ГБ (по умолчанию — из шаблона, иначе 100)")
p.add_argument("--min-rel", type=float, default=market.MIN_REL, help=f"минимальная надёжность ({market.MIN_REL})")
p.add_argument("--stream-gb", type=float, default=market.STREAM_GB, help=f"трафик стрима, ГБ в час ({market.STREAM_GB})")
p.add_argument("--max-traffic", type=float, default=market.MAX_TRAFFIC,
               help="скрыть машины, где трафик дороже, $/ГБ (по умолчанию — не скрывать)")
p.add_argument("--free", action="store_true", help="только машины с бесплатным трафиком (до $0.10 за ТБ)")
p.add_argument("--rows", type=int, default=20, help="сколько строк показать (20)")
a = p.parse_args()
if a.disk is None:
    tpl = my_template()   # размер диска по умолчанию задаётся в мастере и хранится в шаблоне
    a.disk = int((tpl or {}).get("recommended_disk_space") or 100)

rows = market.search(a.disk, a.min_rel, a.free, a.max_traffic, a.stream_gb)

if not rows:
    print("Подходящих машин нет — попробуй ослабить фильтры (--disk, --min-rel, --max-traffic)")
else:
    print(f"Диск {a.disk} ГБ · надёжность от {a.min_rel} · стрим {a.stream_gb:g} ГБ/ч"
          f" · найдено {len(rows)}, показано {min(len(rows), a.rows)}\n")
    market.print_table(rows[:a.rows])
    print("\nЦены в $ за час; аренда уже включает диск. Трафик = стрим × цена исходящего ГБ.")
    print("Арендовать самую выгодную: vastgame play   (или конкретную: vastgame play ID)")
if any(o.get("_failed") for o in rows[:a.rows]):
    print("«!» — на этом хосте машина раньше не запустилась; может не запуститься снова (пометка снимется после удачного запуска).")

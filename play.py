# play.py — арендовать машину по шаблону и дождаться, пока можно играть (терминал).
# Запуск: vastgame play                   — подобрать машину самому (самая выгодная из подходящих)
#         vastgame play 51607424          — конкретная машина (ID из vastgame offers)
#         vastgame play --disk 150 --no-games
#         vastgame play --dry-run         — показать запрос, ничего не арендовать
#
# Вся логика — в core.py (ею же пользуется окно приложения); здесь — разбор параметров,
# выбор машины в терминале и печать итогов.
#  1. Проверка: других инстансов нет (одновременно — только один).
#  2. Машина: подобрать (пятёрка лучших, по умолчанию — первая) или проверить указанную;
#     свежая цена и баланс → вопрос «Арендовать?». Аренда — только после «да».
#  3. Аренда и ожидание: Vast запустил машину → узел vastai-gaming онлайн в Tailscale →
#     wolf-setup.sh восстановил всё из Google Drive (boot = ok на странице /json).
#  4. Адреса для Moonlight и Sunshine.

import argparse
import json
import sys

import core
import market
from cliui import ConsoleUI
from wolfnode import print_links

p = argparse.ArgumentParser(description="Арендовать машину и дождаться готовности")
p.add_argument("offer", type=int, nargs="?", help="ID машины из vastgame offers; без него — подобрать самому")
p.add_argument("--disk", type=int, help="размер диска, ГБ (по умолчанию — из шаблона)")
g = p.add_mutually_exclusive_group()
g.add_argument("--games", dest="games", action="store_const", const="1",
               help="синхронизировать игры Steam с облаком (SYNC_STEAM_GAMES=1)")
g.add_argument("--no-games", dest="games", action="store_const", const="0",
               help="не синхронизировать игры Steam (SYNC_STEAM_GAMES=0); без флага — как в шаблоне")
p.add_argument("--dry-run", action="store_true", help="только показать, что будет отправлено")
a = p.parse_args()
ui = ConsoleUI()

# ---- 1. Одновременно работает только один инстанс
running = core.instances()
if running:
    if not a.dry_run:
        sys.exit(core.running_message(running))
    print(f"({core.running_message(running)} --dry-run всё равно покажет, что было бы арендовано.)\n")

# ---- 2. Шаблон и размер диска по умолчанию из него
tpl = core.template()
if a.disk is None:
    a.disk = core.template_disk(tpl)

# ---- 3. Машина: подобрать или проверить указанную (свежая цена — с нашим диском)
if a.offer is None:
    rows = market.search(a.disk)
    if not rows:
        sys.exit("Подходящих машин сейчас нет. Посмотри шире: vastgame offers --min-rel 0.95")
    top = rows[:5]
    print(f"Подходящих машин: {len(rows)} (RTX/GTX, KVM, надёжность от {market.MIN_REL}, диск {a.disk} ГБ).")
    print("Самые выгодные по полной цене часа (аренда с диском + стрим 15 ГБ):\n")
    market.print_table(top, numbered=True)
    print("\nЗадержка в игре зависит от расстояния до машины — смотри на страну.")
    pick = input(f"Какую? Номер 1–{len(top)}, Enter — №1, «нет» — отмена: ").strip().lower()
    if pick in ("нет", "н", "no", "n", "q"):
        sys.exit("Отменено, ничего не арендовано.")
    if pick and not (pick.isdigit() and 1 <= int(pick) <= len(top)):
        sys.exit("Нет такого номера. Отменено, ничего не арендовано.")
    o = top[int(pick or 1) - 1]
    a.offer = o["id"]
    print()
else:
    o = core.offer_by_id(a.offer, a.disk)
    reason = core.bad_host_reason(o)
    if reason:
        print(f"!!! Этот хост раньше не смог запустить машину: {reason}")

body = core.rental_body(tpl, a.disk, a.games)
credit = core.balance()
games = a.games or core.template_games(tpl)
print(f"Машина {a.offer}: {o['gpu_name']}, {market.country(o)}, надёжность {o['reliability']:.3f}")
print(f"Цена: ${o['dph_total']:.3f}/ч (с диском {a.disk} ГБ); трафик: ↑ ${(o.get('inet_up_cost') or 0) * 1000:.2f}/ТБ, "
      f"↓ ${(o.get('inet_down_cost') or 0) * 1000:.2f}/ТБ")
print(f"Игры Steam в облаке: {'синхронизировать' if games == '1' else 'не синхронизировать'}"
      f"{'' if a.games else ' (как в шаблоне)'}.  Баланс: ${credit:.2f} "
      f"(хватит примерно на {credit / o['dph_total']:.1f} ч)")

if a.dry_run:
    print(f"\n--dry-run: был бы отправлен PUT asks/{a.offer}/ с телом:\n{json.dumps(body, indent=2)}")
    sys.exit(0)

if not ui.confirm("\nАрендовать? Деньги начнут списываться сразу"):
    sys.exit("Отменено, ничего не арендовано.")

# ---- 4. Аренда и ожидание готовности
iid, t0 = core.rent(a.offer, body, o)
print("Ctrl+C прервёт ожидание, но НЕ остановит инстанс — он продолжит работать и тратить деньги.")
result = core.wait_ready(iid, t0, o, ui)

# ---- 5. Готово
if result["boot"][1] == "err":
    print(f"!!! Восстановление с ошибками: {result['boot'][2]}")
    print("    Подробности — на странице статуса (адрес ниже).")
print_links(result["ip"], result["dns"])
for level, text, *_ in core.identity_notes(result):
    print(f"\n  {text}")
print("\nАдреса можно показать снова: vastgame info. Закончил играть — vastgame stop")

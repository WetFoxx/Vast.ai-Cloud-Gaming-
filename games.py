# games.py — игры и сохранения в облаке (Google Drive): список с размерами и удаление ненужных (терминал).
# Запуск: vastgame games                 — список (машину арендовать не нужно)
#         vastgame games rm <название>   — убрать из облака: в корзину Drive, 30 дней можно вернуть
# Логика — в cloudgames.py (ею же пользуется окно приложения).

import argparse
import sys

import cloudgames
from cliui import ask
from vastapi import list_instances

KIND_RU = {"sgame": "игра Steam", "game": "не из Steam", "pfx": "сохранения", "mod": "моды"}

p = argparse.ArgumentParser(description="Игры и сохранения в облаке")
sub = p.add_subparsers(dest="cmd")
rm = sub.add_parser("rm", help="убрать архив из облака (в корзину Drive)")
rm.add_argument("name", help="название из списка (можно часть) или полное имя архива, например sgame--Cyberpunk 2077")
a = p.parse_args()


def size(b):
    """«62.1 ГБ» или «340 МБ» — сохранения обычно весят мегабайты."""
    gb = b / 1e9
    return f"{gb:.1f} ГБ" if gb >= 1 else f"{gb * 1000:.0f} МБ"


def title(g):
    if g["kind"] == "pfx":   # «Baldur's Gate 3 (appid 1086940)»; если Steam игру не знает — просто номер
        return f"{g['title']} (appid {g['appid']})" if g["title"] else f"appid {g['appid']}"
    return g["title"]


data = cloudgames.listing()
games = data["games"]

if a.cmd is None:
    if not games:
        print("Игр и сохранений в облаке нет.")
    else:
        print(f"{'Тип':<13} {'Название':<45} {'Размер':>9}  Изменён")
        for g in games:
            print(f"{KIND_RU[g['kind']]:<13} {title(g)[:45]:<45} {size(g['bytes']):>9}  {g['mtime']}")
        print(f"\nВсего: {size(sum(g['bytes'] for g in games))} в {len(games)} архивах.")
    if data["service"]["names"]:
        print(f"Служебные (Steam, сеть, настройки — не удаляются): {', '.join(data['service']['names'])}; "
              f"{size(data['service']['bytes'])}.")
    print("\nУбрать из облака: vastgame games rm и часть названия, например: vastgame games rm dawnwalker")
    sys.exit(0)

# ---- rm: найти архив по имени или его части
found = cloudgames.find(a.name, games)
if not found:
    sys.exit(f"В облаке нет игры или сохранений «{a.name}». Список: vastgame games")
if len(found) > 1:
    print("Подходит несколько — уточни название:")
    for g in found:
        print(f"  {KIND_RU[g['kind']]:<13} {title(g)}   ({g['name']})")
    sys.exit(1)
g = found[0]
print(f"{KIND_RU[g['kind']]}: {title(g)}, {size(g['bytes'])} (архив {g['name']})")
if g["kind"] == "pfx":
    print("!!! Это СОХРАНЕНИЯ игры (префикс Proton). Без них прогресс в этой игре начнётся заново,")
    print("    если у игры нет своих облачных сохранений Steam.")
print("Файлы уйдут в корзину Google Drive — оттуда их можно вернуть в течение 30 дней.")
if not ask("Убрать из облака?"):
    sys.exit("Отменено.")
if list_instances():
    print("Машина работает — удаляю через wolf на ней (если появится ссылка Tailscale — подтверди вход)...")
how = cloudgames.remove(g["name"])
print(f"✓ Убрано из облака: {title(g)}. Вернуть: корзина Google Drive, папка vastai-cloud-games.")
if how == "machine":
    print("  На машине игра остаётся до её удаления, но выгружаться больше не будет.")

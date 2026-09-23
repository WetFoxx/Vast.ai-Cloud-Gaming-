# games.py — игры и сохранения в облаке (Google Drive): список с размерами и удаление ненужных.
# Запуск: vastgame games                 — список (машину арендовать не нужно)
#         vastgame games rm <название>   — убрать из облака: в корзину Drive, 30 дней можно вернуть
#
# Если машина сейчас работает, удаление идёт через `wolf forget` на ней: wolf запоминает,
# что архив убран, и не выгружает игру заново. Без машины — напрямую через rclone.

import argparse
import shlex   # безопасно подставить имя с пробелами в команду для машины
import sys

import requests

import drive
from vastapi import list_instances
from wolfnode import ssh_run, ts_node

KINDS = {"sgame": "игра Steam", "game": "не из Steam", "pfx": "сохранения"}

p = argparse.ArgumentParser(description="Игры и сохранения в облаке")
sub = p.add_subparsers(dest="cmd")
rm = sub.add_parser("rm", help="убрать архив из облака (в корзину Drive)")
rm.add_argument("name", help="название из списка (можно часть) или полное имя архива, например sgame--Cyberpunk 2077")
a = p.parse_args()


def ask(question):
    try:
        import readline  # noqa: F401 — стрелки и Backspace в ответе (на Windows модуля нет — не страшно)
    except ImportError:
        pass
    return input(f"{question} [да/нет]: ").strip().lower() in ("да", "yes", "y", "д")


def size(gb):
    """«62.1 ГБ» или «340 МБ» — сохранения обычно весят мегабайты."""
    return f"{gb:.1f} ГБ" if gb >= 1 else f"{gb * 1000:.0f} МБ"


def steam_names(appids):
    """Названия игр Steam по номерам (appid) — публичный API магазина Steam, без ключа."""
    names = {}
    for appid in appids:
        try:
            r = requests.get("https://store.steampowered.com/api/appdetails",
                             params={"appids": appid, "filters": "basic"}, timeout=10)
            d = r.json().get(appid) or {}
            if d.get("success"):
                names[appid] = d["data"]["name"]
        except Exception:
            pass
    return names


def game_archives():
    """[(имя архива, тип, название для человека, ГБ, дата, файлы)] — только игры и сохранения."""
    if not drive.token():
        sys.exit("Нет токена Google Drive — пройди первую настройку: vastgame setup")
    arch = drive.archives()
    if arch is None:
        sys.exit("Не удалось открыть Google Drive — проверь интернет или пройди шаг 2: vastgame setup")
    names = steam_names([n[5:] for n in arch if n.startswith("pfx--")])
    rows = []
    for base, info in arch.items():
        kind = base.split("--")[0] if "--" in base else None
        if kind not in KINDS:
            continue   # служебные архивы (identity, steam-state, ...) — не для удаления
        rest = base.split("--", 1)[1]
        title = f"{names.get(rest, 'appid ' + rest)} (appid {rest})" if kind == "pfx" else rest
        rows.append((base, KINDS[kind], title, info["size"] / 1e9, info["mtime"], info["files"]))
    return sorted(rows, key=lambda r: (r[1], -r[3])), arch


if a.cmd is None:
    rows, arch = game_archives()
    service = {n: v for n, v in arch.items() if not any(n.startswith(k + "--") for k in KINDS)}
    if not rows:
        print("Игр и сохранений в облаке нет.")
    else:
        print(f"{'Тип':<13} {'Название':<45} {'Размер':>9}  Изменён")
        for base, kind, title, gb, mtime, files in rows:
            print(f"{kind:<13} {title[:45]:<45} {size(gb):>9}  {mtime}")
        print(f"\nВсего: {size(sum(r[3] for r in rows))} в {len(rows)} архивах.")
    if service:
        gb = sum(v["size"] for v in service.values()) / 1e9
        print(f"Служебные (Steam, сеть, настройки — не удаляются): {', '.join(sorted(service))}; {size(gb)}.")
    print("\nУбрать из облака: vastgame games rm <название>")
    sys.exit(0)

# ---- rm: найти архив по имени или его части
rows, _ = game_archives()
q = a.name.lower()
found = [r for r in rows if r[0].lower() == q] or [r for r in rows if q in r[0].lower() or q in r[2].lower()]
if not found:
    sys.exit(f"В облаке нет игры или сохранений «{a.name}». Список: vastgame games")
if len(found) > 1:
    print("Подходит несколько — уточни название:")
    for r in found:
        print(f"  {r[1]:<13} {r[2]}   ({r[0]})")
    sys.exit(1)
base, kind, title, gb, mtime, files = found[0]

print(f"{kind}: {title}, {size(gb)} (архив {base})")
if kind == "сохранения":
    print("!!! Это СОХРАНЕНИЯ игры (префикс Proton). Без них прогресс в этой игре начнётся заново,")
    print("    если у игры нет своих облачных сохранений Steam.")
print("Файлы уйдут в корзину Google Drive — оттуда их можно вернуть в течение 30 дней.")
if not ask("Убрать из облака?"):
    sys.exit("Отменено.")

inst = list_instances()
node = ts_node() if inst else None
if inst and node:
    # Машина работает — через wolf: он пометит архив и не выгрузит его заново
    _, ip, _ = node
    print("Машина работает — удаляю через wolf на ней (если появится ссылка Tailscale — подтверди вход)...")
    code, out = ssh_run(ip, f"wolf forget {shlex.quote(base)}", timeout=300)
    if code != 0:
        sys.exit(f"wolf forget не сработал (код {code}): {out.strip()[-300:]}")
elif inst:
    sys.exit("Машина арендована, но её узла нет в Tailscale — удалять мимо неё нельзя:\n"
             "wolf на ней выгрузил бы игру заново. Дождись запуска (vastgame info) или останови её.")
else:
    if not drive.trash(files):
        sys.exit("Не все файлы удалось убрать — запусти команду ещё раз.")

# Проверка: в облаке архива больше нет
left = drive.archives() or {}
if base in left:
    sys.exit(f"!!! {base} всё ещё в облаке — проверь на Drive.")
print(f"✓ Убрано из облака: {title}. Вернуть: корзина Google Drive, папка {drive.FOLDER}.")
if inst:
    print("  На машине игра остаётся до её удаления, но выгружаться больше не будет.")

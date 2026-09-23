# vastgame.py — одна точка входа: vastgame <команда> [параметры].
# Каждая команда — отдельный файл в этой папке; здесь только выбор, какой из них запустить.
#   vastgame offers --free       то же, что .venv/bin/python offers.py --free

import runpy   # запустить другой .py-файл так, будто его вызвали напрямую
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

COMMANDS = {
    "setup":   ("setup.py",   "первая настройка: ключи Vast, Google Drive, Tailscale"),
    "balance": ("balance.py", "баланс Vast"),
    "offers":  ("offers.py",  "подходящие машины (--free — только с бесплатным трафиком)"),
    "play":    ("play.py",    "подобрать и арендовать машину, дождаться готовности"),
    "migrate": ("migrate.py", "перенести архивы из старой папки (до своего OAuth-клиента) в папку приложения"),
    "games":   ("games.py",   "игры и сохранения в облаке; убрать: vastgame games rm часть-названия"),
    "info":    ("info.py",    "адреса работающей машины: Moonlight, кабинет Sunshine, статус"),
    "stop":    ("stop.py",    "выгрузить всё в облако и удалить машину"),
}


def usage():
    print("vastgame — пульт облачного игрового ПК на Vast.ai\n")
    print("Команды:")
    for name, (_, text) in COMMANDS.items():
        print(f"  vastgame {name:<8} {text}")
    print("\nПараметры команды: vastgame <команда> --help")


if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
    usage()
    sys.exit(0)

name = sys.argv[1]
if name not in COMMANDS:
    usage()
    sys.exit(f"\nНет такой команды: {name}")

script = HERE / COMMANDS[name][0]
# Для запущенного файла всё выглядит так, будто его вызвали напрямую: argv[0] — он сам,
# дальше — параметры, которые пришли после имени команды
sys.argv = [f"vastgame {name}"] + sys.argv[2:]
runpy.run_path(str(script), run_name="__main__")

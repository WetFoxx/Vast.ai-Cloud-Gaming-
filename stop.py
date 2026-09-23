# stop.py — выгрузить всё с инстанса в Google Drive и только потом удалить его (терминал).
# Запуск: vastgame stop
#         vastgame stop --dry-run   (проверить связь и статус, ничего не выгружать и не удалять)
#
# Вся логика — в core.stop (ею же пользуется окно приложения):
#  1. Находим инстанс в Vast и его узел в Tailscale; проверяем Tailscale SSH безвредной командой.
#  2. По Tailscale SSH запускаем `wolf shutdown`: он закрывает Steam и сразу выгружает всё.
#  3. Проверяем результат по /json, а не по коду выхода: wolf shutdown всегда отвечает 0.
#  4. Всё выгружено → удаляем сразу, без вопроса. Нет подтверждения выгрузки → НЕ удаляем.

import argparse

import core
from cliui import ConsoleUI

p = argparse.ArgumentParser(description="Выгрузить всё и удалить инстанс")
p.add_argument("--dry-run", action="store_true", help="только проверить связь и статус")
a = p.parse_args()

result = core.stop(ConsoleUI(), dry_run=a.dry_run)
if a.dry_run:
    print("\nАрхивы сейчас:")
    for name, (ts, state, text) in sorted(result["archives"].items()):
        print(f"  {name:<28} {state:<5} {text}")
    print("\n--dry-run: shutdown не запускаю, ничего не удаляю.")

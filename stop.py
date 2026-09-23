# stop.py — выгрузить всё с инстанса в Google Drive и только потом удалить его.
# Запуск: .venv/bin/python stop.py
#         .venv/bin/python stop.py --dry-run   (проверить связь и статус, ничего не выгружать и не удалять)
#
# Что происходит:
#  1. Находим инстанс в Vast и его узел в Tailscale.
#  2. По Tailscale SSH запускаем `wolf shutdown`: он закрывает Steam и сразу выгружает всё.
#     Пока он работает — показываем, как меняются архивы на странице /json.
#  3. Проверяем результат по /json, а не по коду выхода: wolf shutdown всегда отвечает 0.
#     Учитываем только записи, появившиеся после запуска shutdown: старые ошибки живут
#     в /json до перезагрузки и к этой выгрузке отношения не имеют.
#  4. Всё выгружено → вопрос «Удалить?». Нет подтверждения выгрузки → НЕ удаляем.

import argparse
import sys
import time

from vastapi import call, list_instances
from wolfnode import ssh, ssh_run, ts_node, wolf_status

p = argparse.ArgumentParser(description="Выгрузить всё и удалить инстанс")
p.add_argument("--dry-run", action="store_true", help="только проверить связь и статус")
a = p.parse_args()


def ask(question):
    try:
        import readline  # noqa: F401 — стрелки и Backspace в ответе (на Windows модуля нет)
    except ImportError:
        pass
    return input(f"{question} [да/нет]: ").strip().lower() in ("да", "yes", "y", "д")


# ---- 1. Инстанс и узел
inst = list_instances()
if not inst:
    sys.exit("Инстансов нет — останавливать нечего.")
if len(inst) > 1:
    sys.exit(f"Инстансов несколько ({', '.join(str(i['id']) for i in inst)}) — разберись в консоли Vast.")
iid = inst[0]["id"]

node = ts_node()
if not node:
    sys.exit(f"Инстанс {iid} есть, но узла vastai-gaming в Tailscale нет — выгрузить нечем.\n"
             "НЕ удаляю. Проверь машину через консоль Vast (Open → терминал).")
_, ip, dns = node
print(f"Инстанс {iid}, узел {dns or ip}")

before = wolf_status(ip)
if before is None:
    sys.exit("Страница статуса wolf не отвечает — НЕ удаляю. Проверь машину.")
boot = before.get("boot")
if not boot or boot[1] != "ok":
    print(f"!!! Загрузка ещё не закончена или с ошибкой: boot = {boot[1] if boot else 'нет'} "
          f"{boot[2] if boot else ''}")

# Проверка SSH безвредной командой — до того, как что-то запускать.
# Если Tailscale попросит подтвердить вход — ссылка появится здесь; ждём до 3 минут
print("Проверяю Tailscale SSH (если появится ссылка — открой её и подтверди вход)...")
code, out = ssh_run(ip, "echo ok")
if code != 0 or out.strip() != "ok":
    sys.exit(f"Tailscale SSH не работает (код {code}): {out.strip()[:200]}\n"
             "НЕ удаляю. Выгрузи вручную на машине: sudo wolf shutdown")
print("Tailscale SSH: работает.")

if a.dry_run:
    print("\nАрхивы сейчас:")
    for name, (ts, state, text) in sorted(before.items()):
        print(f"  {name:<28} {state:<5} {text}")
    print("\n--dry-run: shutdown не запускаю, ничего не удаляю.")
    sys.exit(0)

# ---- 2. wolf shutdown; время берём с машины, чтобы сравнивать с ts в /json по одним часам
code, out = ssh_run(ip, "date +%s", timeout=60)
if code != 0 or not out.strip().isdigit():
    sys.exit("Не удалось узнать время на машине — НЕ запускаю выгрузку.")
t0 = int(out.strip()) - 2
print("Закрываю Steam и выгружаю всё в Google Drive (может занять несколько минут)...")
proc = ssh(ip, "wolf shutdown")
seen = {}
deadline = time.time() + 30 * 60
last_check = time.time()
while True:
    # Раз в минуту — жив ли инстанс: его могли удалить в консоли Vast посреди выгрузки
    if time.time() - last_check > 60:
        last_check = time.time()
        if iid not in [i["id"] for i in list_instances()]:
            proc.kill()
            sys.exit(f"\n!!! Инстанс {iid} удалён во время выгрузки (не этой командой).\n"
                     "Что успело выгрузиться — выше; остальное не дошло до Drive.")
    s = wolf_status(ip) or {}
    for name, (ts, state, text) in sorted(s.items()):
        if ts >= t0 and seen.get(name) != (state, text):
            print(f"  {name:<28} {state:<5} {text}")
            seen[name] = (state, text)
    if proc.poll() is not None:
        break
    if time.time() > deadline:
        proc.kill()
        sys.exit("!!! wolf shutdown не закончился за 30 минут — НЕ удаляю. Посмотри /var/log/wolf.log.")
    time.sleep(5)
out = proc.stdout.read()
if proc.returncode != 0:
    sys.exit(f"!!! SSH/wolf shutdown завершился с кодом {proc.returncode}: {out.strip()[-300:]}\nНЕ удаляю.")

# ---- 3. Проверка по /json
after = wolf_status(ip)
if after is None:
    sys.exit("!!! Страница статуса не отвечает после выгрузки — НЕ удаляю.")
fresh = {n: v for n, v in after.items() if v[0] >= t0 and n != "boot"}
bad = {n: v for n, v in fresh.items() if v[1] in ("up", "down", "err")}
old_err = {n: v for n, v in after.items() if v[0] < t0 and v[1] == "err"}
uploaded = [n for n, v in fresh.items() if v[1] == "ok"]

print(f"\nВыгружено в этот раз: {len(uploaded)} архив(ов)"
      + (f": {', '.join(sorted(uploaded))}" if uploaded else " (изменений не было — всё уже в облаке)"))
if old_err:
    print("Старые ошибки (до shutdown, к этой выгрузке не относятся):")
    for n, (ts, st, tx) in sorted(old_err.items()):
        print(f"  {n}: {tx}")
if bad:
    print("\n!!! Выгрузка НЕ подтверждена:")
    for n, (ts, st, tx) in sorted(bad.items()):
        print(f"  {n:<28} {st:<5} {tx}")
    sys.exit("НЕ удаляю инстанс. Разберись на машине (/var/log/wolf.log), затем запусти stop.py снова.")

# ---- 4. Удаление — только после «да»
print("\nВсё выгружено.")
if not ask(f"Удалить инстанс {iid}? Диск машины будет стёрт"):
    sys.exit("Не удаляю. Инстанс продолжает работать и тратить деньги.")
r = call("DELETE", f"instances/{iid}/", {})
if not r.get("success"):
    sys.exit(f"Vast не удалил инстанс: {r.get('msg')}")
time.sleep(5)
left = [i["id"] for i in list_instances()]
print(f"Инстанс {iid} удалён." if iid not in left else f"Vast принял удаление, но {iid} ещё в списке — проверь через минуту.")

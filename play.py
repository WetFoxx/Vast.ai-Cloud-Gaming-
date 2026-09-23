# play.py — арендовать машину по шаблону и дождаться, пока можно играть.
# Запуск: vastgame play                   — подобрать машину самому (самая выгодная из подходящих)
#         vastgame play 51607424          — конкретная машина (ID из vastgame offers)
#         vastgame play --disk 150 --no-games
#         vastgame play --dry-run         — показать запрос, ничего не арендовать
#
# Что происходит:
#  1. Проверка: других инстансов нет (одновременно — только один).
#  2. Машина: подобрать (пятёрка лучших, по умолчанию — первая) или проверить указанную;
#     свежая цена и баланс → вопрос «Арендовать?». Аренда — только после «да».
#  3. PUT asks/<id>/ — аренда по шаблону.
#  4. Ожидание: Vast запустил машину → узел vastai-gaming онлайн в Tailscale →
#     wolf-setup.sh восстановил всё из Google Drive (boot = ok на странице /json).
#  5. Адрес для Moonlight.

import argparse
import json
import re
import sys
import time

import market
from vastapi import TEMPLATE_NAME, call, list_instances, my_template
from wolfnode import NODE, print_links, ts_node, wolf_status


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


def ask(question):
    """Задать вопрос; True — только если человек напечатал «да»."""
    try:
        import readline  # noqa: F401 — стрелки и Backspace в ответе (на Windows модуля нет)
    except ImportError:
        pass
    return input(f"{question} [да/нет]: ").strip().lower() in ("да", "yes", "y", "д")


def fail(instance_id, why, bad_host=False):
    """Что-то пошло не так после аренды: машина уже платная — предложить удалить.
    bad_host — виноват хост: запомнить его, чтобы больше не предлагать."""
    print(f"\n!!! {why}")
    if bad_host:
        market.mark_bad(o.get("machine_id"), why)
        print(f"Этот хост (machine {o.get('machine_id')}) больше не будет предлагаться.")
    print(f"Инстанс {instance_id} арендован и тратит деньги.")
    if ask("Удалить его сейчас? На нём ещё ничего не сохранялось"):
        r = call("DELETE", f"instances/{instance_id}/", {})
        print("Удалён." if r.get("success") else f"Vast не удалил: {r.get('msg')}")
    else:
        print("Не удаляю. Удалить можно в консоли Vast (Instances → корзина).")
    if bad_host:
        print("Попробовать другую машину: vastgame play")
    sys.exit(1)


# ---- 1. Одновременно работает только один инстанс
running = list_instances()
if running:
    ids = ", ".join(str(i["id"]) for i in running)
    msg = f"У тебя уже есть инстанс ({ids}). Одновременно — только один: общие архивы и личность Tailscale."
    if not a.dry_run:
        sys.exit(msg)
    print(f"({msg} --dry-run всё равно покажет, что было бы арендовано.)\n")

# ---- 2. Шаблон (создаёт мастер) и размер диска по умолчанию из него
tpl = my_template()
if not tpl:
    sys.exit(f"Нет шаблона «{TEMPLATE_NAME}» — создай его: vastgame setup")
if a.disk is None:
    a.disk = int(tpl.get("recommended_disk_space") or 100)

# ---- 3. Машина: подобрать самим или проверить указанную (свежая цена — с нашим диском)
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
    # Поле id в фильтре поиска не работает (молча находит 0), а ask_contract_id — работает
    found = call("POST", "bundles/", {
        "ask_contract_id": {"eq": a.offer}, "rentable": {"eq": True}, "type": "ondemand",
        "allocated_storage": a.disk, "limit": 1,
    }).get("offers", [])
    if not found:
        sys.exit(f"Машина {a.offer} уже недоступна — подбери другую: vastgame play")
    o = found[0]
    if not o.get("vms_enabled"):
        sys.exit(f"Машина {a.offer} не поддерживает KVM — шаблон на ней не запустится.")
    if o.get("disk_space", 0) < a.disk:
        sys.exit(f"На машине свободно {o['disk_space']:.0f} ГБ, а нужно {a.disk}.")
    if str(o.get("machine_id")) in market.bad_hosts():
        print(f"!!! Этот хост раньше не смог запустить машину: {market.bad_hosts()[str(o.get('machine_id'))]['reason']}")
credit = call("GET", "users/current/").get("credit", 0)

body = {
    "client_id": "me",
    "template_hash_id": tpl["hash_id"],
    "disk": a.disk,
    "label": "vastgame",
}
if a.games is not None:
    # Перекрывает значение из шаблона только для этой аренды; wolf-setup.sh читает его при старте
    body["env"] = {"SYNC_STEAM_GAMES": a.games}
tpl_games = "1" if re.search(r"-e\s+SYNC_STEAM_GAMES=\"?1", tpl.get("env") or "") else "0"

print(f"Машина {a.offer}: {o['gpu_name']}, {market.country(o)}, "
      f"надёжность {o['reliability']:.3f}")
print(f"Цена: ${o['dph_total']:.3f}/ч (с диском {a.disk} ГБ); трафик: ↑ ${o.get('inet_up_cost', 0) * 1000:.2f}/ТБ, "
      f"↓ ${o.get('inet_down_cost', 0) * 1000:.2f}/ТБ")
games = a.games or tpl_games
print(f"Игры Steam в облаке: {'синхронизировать' if games == '1' else 'не синхронизировать'}"
      f"{'' if a.games else ' (как в шаблоне)'}.  Баланс: ${credit:.2f} "
      f"(хватит примерно на {credit / o['dph_total']:.1f} ч)")

if a.dry_run:
    print(f"\n--dry-run: был бы отправлен PUT asks/{a.offer}/ с телом:\n{json.dumps(body, indent=2)}")
    sys.exit(0)

if not ask("\nАрендовать? Деньги начнут списываться сразу"):
    sys.exit("Отменено, ничего не арендовано.")

# ---- 4. Аренда
t0 = time.time()
r = call("PUT", f"asks/{a.offer}/", body)
iid = r.get("new_contract")
if not r.get("success") or not iid:
    sys.exit(f"Vast не арендовал машину: {r.get('msg') or r.get('error') or 'без объяснения'}")
print(f"Арендовано: инстанс {iid}. Жду запуска (обычно 3–15 минут: машина качает образ).")
print("Ctrl+C прервёт ожидание, но НЕ остановит инстанс — он продолжит работать и тратить деньги.")

# ---- 5a. Vast запускает машину
last = None
errors = 0
deadline = time.time() + 30 * 60
while True:
    # Разовый сбой API не должен обрывать ожидание: машина уже платная, следим дальше
    try:
        inst = call("GET", f"instances/{iid}/", params={"owner": "me"}).get("instances") or {}
    except SystemExit as e:
        print(f"  (Vast не ответил: {e} — пробую ещё раз)")
        inst = {"actual_status": last}
    st = inst.get("actual_status")
    if st != last:
        print(f"  Vast: {st or 'создаётся'}  {(inst.get('status_msg') or '').strip()[:80]}")
        last = st
    if st == "running":
        # Диагностика: что реально попало на машину. Только ИМЕНА переменных — значения
        # (ключи Tailscale и Google) не печатаем. Сравнить с тем, что ждёт wolf-setup.sh.
        # Порты (-p ...) тоже лежат в extra_env — их пропускаем. Секретные переменные
        # (ключи из аккаунта, OPEN_BUTTON_TOKEN) API не показывает — их отсутствие здесь нормально.
        names = sorted(e[0] for e in inst.get("extra_env") or [] if e and not e[0].startswith("-"))
        print(f"  Переменные на машине (кроме секретных): {', '.join(names) or 'НЕТ'}")
        for k in sorted(inst):
            if "onstart" in k or "template" in k:
                v = inst[k]
                print(f"  {k}: " + (f"{len(v)} символов" if isinstance(v, str) and len(v) > 40 else repr(v)))
        ip_pub = inst.get("public_ipaddr") or "?"
        break
    # Из этих состояний машина уже не дойдёт до running (см. документацию Vast)
    if st in ("exited", "offline", "unknown"):
        fail(iid, f"Машина перешла в состояние «{st}» и не запустится.", bad_host=True)
    # Хост не смог создать машину: «Error response from daemon ...» в status_msg.
    # Ошибку, повторившуюся две проверки подряд (~30 с), считаем окончательной.
    err = "error" in (inst.get("status_msg") or "").lower()
    errors = errors + 1 if err else 0
    if errors >= 2:
        fail(iid, "Хост не смог запустить машину: " + inst["status_msg"].strip()[:200], bad_host=True)
    if time.time() > deadline:
        fail(iid, "Машина не запустилась за 30 минут.", bad_host=True)
    time.sleep(15)

# ---- 5b. Узел появляется в Tailscale (wolf-setup.sh поднимает его с восстановленной личностью)
print("Машина запущена. Жду узел vastai-gaming в Tailscale (обычно 5–7 минут).")
started = time.time()
deadline = started + 20 * 60
warned = False
while True:
    # Обычно узел появляется за 5–7 минут; через 10 — подсказать, где искать причину
    if not warned and time.time() - started > 10 * 60:
        warned = True
        print(f"\n  Узла нет уже 10 минут — это дольше обычного. Публичный IP машины: {ip_pub}")
        print("  Посмотри на машине (консоль Vast → Instances → Open → терминал):")
        print("    tail -n 30 /var/log/wolf-setup.log     # установка")
        print("    tail -n 30 /var/log/wolf.log           # восстановление из Drive")
        print("  Жду ещё до 20 минут.\n")
    node = ts_node()
    if node and node[0] and wolf_status(node[1]) is not None:
        online, ip, dns = node
        break
    if time.time() > deadline:
        print("\n!!! Узел не появился в Tailscale за 20 минут. Инстанс работает и тратит деньги.")
        print("Посмотри лог в консоли Vast или через SSH Vast: /var/log/wolf-setup.log")
        sys.exit(1)
    time.sleep(15)
print(f"Tailscale: {dns or ip} ({ip}) онлайн.")

# ---- 5c. wolf восстанавливает всё из Google Drive; показываем, как меняются архивы
print("Восстановление из Google Drive:")
seen = {}
while True:
    s = wolf_status(ip) or {}
    for name, (ts, state, text) in sorted(s.items()):
        if ts >= t0 - 60 and seen.get(name) != (state, text):
            print(f"  {name:<28} {state:<5} {text}")
            seen[name] = (state, text)
    boot = s.get("boot")
    if boot and boot[0] >= t0 - 60 and boot[1] in ("ok", "err"):
        break
    time.sleep(10)

# ---- 6. Готово
print()
if boot[1] == "err":
    print(f"!!! Восстановление с ошибками: {boot[2]}")
    print("    Подробности — на странице статуса (адрес ниже).")
print(f"Готово за {int((time.time() - t0) / 60)} мин.")
print_links(ip, dns)
# Личность Tailscale: смотрим на статус identity, а не на имя. Номер в имени (vastai-gaming-5)
# бывает и у восстановленного узла — если прежние имена заняты старыми узлами в сети
ident = (wolf_status(ip) or {}).get("identity")
short = dns.split(".")[0] if dns else ""
if not ident or ident[1] == "err" or "нет в облаке" in ident[2]:
    print(f"\n  !!! Личность Tailscale не восстановилась ({ident[2] if ident else 'нет данных'}): это новый узел,")
    print("      и в Moonlight его придётся добавить заново. Старый узел можно удалить в админке Tailscale.")
elif short != NODE:
    print(f"\n  Узел прежний, но имя с номером ({short}): имя {NODE} занято старым узлом в сети.")
    print("  Удали лишние офлайн-узлы vastai-gaming в админке Tailscale — имя станет короче со следующей аренды.")
print("\nАдреса можно показать снова: vastgame info. Закончил играть — vastgame stop")

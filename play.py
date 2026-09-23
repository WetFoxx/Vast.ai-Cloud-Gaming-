# play.py — арендовать машину по шаблону и дождаться, пока можно играть.
# Запуск: .venv/bin/python play.py <ID машины из offers.py>
#         .venv/bin/python play.py 51607424 --disk 150 --no-games
#         .venv/bin/python play.py 51607424 --dry-run   (показать запрос, ничего не арендовать)
#
# Что происходит:
#  1. Проверка: других инстансов нет (одновременно — только один).
#  2. Свежая цена машины и баланс → вопрос «Арендовать?». Аренда — только после «да».
#  3. PUT asks/<id>/ — аренда по шаблону.
#  4. Ожидание: Vast запустил машину → узел vastai-gaming онлайн в Tailscale →
#     wolf-setup.sh восстановил всё из Google Drive (boot = ok на странице /json).
#  5. Адрес для Moonlight.

import argparse
import json
import subprocess   # запуск программ на Mac — здесь команды tailscale
import sys
import time

import requests

from vastapi import call, list_instances

TEMPLATE = "49faada2e6e7fe3e15bef0ea1999420a"   # шаблон WETFOX CLOUD GAME
NODE = "vastai-gaming"                          # имя узла в Tailscale (TAILSCALE_HOSTNAME в wolf-setup.sh)
WOLF_PORT = 8099                                # страница статуса wolf на инстансе

p = argparse.ArgumentParser(description="Арендовать машину и дождаться готовности")
p.add_argument("offer", type=int, help="ID машины из таблицы offers.py")
p.add_argument("--disk", type=int, default=100, help="размер диска, ГБ (100)")
p.add_argument("--no-games", action="store_true", help="не загружать игры из облака (SYNC_STEAM_GAMES=0)")
p.add_argument("--dry-run", action="store_true", help="только показать, что будет отправлено")
a = p.parse_args()


def ask(question):
    """Задать вопрос; True — только если человек напечатал «да»."""
    return input(f"{question} [да/нет]: ").strip().lower() in ("да", "yes", "y", "д")


def ts_node():
    """Найти узел NODE в Tailscale на Mac, который сейчас онлайн. Возвращает (True, IP, DNS-имя) или None.

    Узлов с именем vastai-gaming в сети может быть несколько — старые, от прошлых аренд
    (их DNS-имена vastai-gaming-1, -2...). Нам нужен тот, что онлайн.
    """
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=20)
        peers = json.loads(out.stdout).get("Peer") or {}
    except Exception:
        return None
    for peer in peers.values():
        if peer.get("HostName") == NODE and peer.get("Online"):
            ips = peer.get("TailscaleIPs") or [""]
            return True, ips[0], (peer.get("DNSName") or "").rstrip(".")
    return None


def wolf_status(ip):
    """Статусы архивов со страницы /json на инстансе: {имя: (время, состояние, текст)}."""
    try:
        r = requests.get(f"http://{ip}:{WOLF_PORT}/json", timeout=5)
        return {x["name"]: (x["ts"], x["state"], x["text"]) for x in r.json()}
    except Exception:
        return None


def fail(instance_id, why):
    """Что-то пошло не так после аренды: машина уже платная — предложить удалить."""
    print(f"\n!!! {why}")
    print(f"Инстанс {instance_id} арендован и тратит деньги.")
    if ask("Удалить его сейчас? На нём ещё ничего не сохранялось"):
        r = call("DELETE", f"instances/{instance_id}/", {})
        print("Удалён." if r.get("success") else f"Vast не удалил: {r.get('msg')}")
    else:
        print("Не удаляю. Удалить можно в консоли Vast (Instances → корзина).")
    sys.exit(1)


# ---- 1. Одновременно работает только один инстанс
running = list_instances()
if running:
    ids = ", ".join(str(i["id"]) for i in running)
    sys.exit(f"У тебя уже есть инстанс ({ids}). Одновременно — только один: общие архивы и личность Tailscale.")

# ---- 2. Свежая цена этой машины (с нашим диском) и баланс
# Поле id в фильтре поиска не работает (молча находит 0), а ask_contract_id — работает
found = call("POST", "bundles/", {
    "ask_contract_id": {"eq": a.offer}, "rentable": {"eq": True}, "type": "ondemand",
    "allocated_storage": a.disk, "limit": 1,
}).get("offers", [])
if not found:
    sys.exit(f"Машина {a.offer} уже недоступна — запусти offers.py заново.")
o = found[0]
if not o.get("vms_enabled"):
    sys.exit(f"Машина {a.offer} не поддерживает KVM — шаблон на ней не запустится.")
if o.get("disk_space", 0) < a.disk:
    sys.exit(f"На машине свободно {o['disk_space']:.0f} ГБ, а нужно {a.disk}.")
credit = call("GET", "users/current/").get("credit", 0)

body = {
    "client_id": "me",
    "template_hash_id": TEMPLATE,
    "disk": a.disk,
    "label": "vastgame",
    # Добавляется к переменным шаблона и аккаунта; wolf-setup.sh читает его при старте
    "env": {"SYNC_STEAM_GAMES": "0" if a.no_games else "1"},
}

print(f"Машина {a.offer}: {o['gpu_name']}, {(o.get('geolocation') or '?').strip(', ')}, "
      f"надёжность {o['reliability']:.3f}")
print(f"Цена: ${o['dph_total']:.3f}/ч (с диском {a.disk} ГБ); трафик: ↑ ${o.get('inet_up_cost', 0) * 1000:.2f}/ТБ, "
      f"↓ ${o.get('inet_down_cost', 0) * 1000:.2f}/ТБ")
print(f"Игры из облака: {'НЕ загружать' if a.no_games else 'загружать'}.  Баланс: ${credit:.2f} "
      f"(хватит примерно на {credit / o['dph_total']:.1f} ч)")

if a.dry_run:
    print(f"\n--dry-run: был бы отправлен PUT asks/{a.offer}/ с телом:\n{json.dumps(body, indent=2)}")
    sys.exit(0)

if not ask("\nАрендовать? Деньги начнут списываться сразу"):
    sys.exit("Отменено, ничего не арендовано.")

# ---- 3. Аренда
t0 = time.time()
r = call("PUT", f"asks/{a.offer}/", body)
iid = r.get("new_contract")
if not r.get("success") or not iid:
    sys.exit(f"Vast не арендовал машину: {r.get('msg') or r.get('error') or 'без объяснения'}")
print(f"Арендовано: инстанс {iid}. Жду запуска (обычно 3–15 минут: машина качает образ).")
print("Ctrl+C прервёт ожидание, но НЕ остановит инстанс — он продолжит работать и тратить деньги.")

# ---- 4a. Vast запускает машину
last = None
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
        break
    # Из этих состояний машина уже не дойдёт до running (см. документацию Vast)
    if st in ("exited", "offline", "unknown"):
        fail(iid, f"Машина перешла в состояние «{st}» и не запустится.")
    if time.time() > deadline:
        fail(iid, "Машина не запустилась за 30 минут.")
    time.sleep(15)

# ---- 4b. Узел появляется в Tailscale (wolf-setup.sh поднимает его с восстановленной личностью)
print("Машина запущена. Жду узел vastai-gaming в Tailscale (установка пакетов, 3–10 минут).")
deadline = time.time() + 20 * 60
while True:
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

# ---- 4c. wolf восстанавливает всё из Google Drive; показываем, как меняются архивы
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

# ---- 5. Готово
print()
if boot[1] == "err":
    print(f"!!! Восстановление с ошибками: {boot[2]}")
    print(f"    Подробности: http://{ip}:{WOLF_PORT}")
print(f"Готово за {int((time.time() - t0) / 60)} мин. В Moonlight добавь ПК: {ip}" + (f"  ({dns})" if dns else ""))
print(f"Страница статуса: http://{ip}:{WOLF_PORT}")

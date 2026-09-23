# info.py — адреса работающей машины: Moonlight, кабинет Sunshine, статус; и состояние архивов.
# Запуск: vastgame info

import sys

from vastapi import list_instances
from wolfnode import print_links, ts_node, wolf_status

inst = list_instances()
if not inst:
    sys.exit("Машин нет. Арендовать: vastgame offers → vastgame play <ID>")
i = inst[0]
print(f"Инстанс {i['id']}: {i.get('gpu_name', '?')}, {(i.get('geolocation') or '?').strip(', ')}, "
      f"${(i.get('dph_total') or 0):.3f}/ч, Vast: {i.get('actual_status') or '?'}")

node = ts_node()
if not node:
    sys.exit("Узла vastai-gaming в Tailscale пока нет — машина ещё запускается или что-то пошло не так.")
_, ip, dns = node
print_links(ip, dns)

st = wolf_status(ip)
if st is None:
    sys.exit("\nСтраница статуса wolf пока не отвечает.")
busy = [n for n, v in st.items() if v[1] in ("up", "down")]
errs = [n for n, v in st.items() if v[1] == "err"]
boot = st.get("boot")
print(f"\nЗагрузка: {boot[1] + ' — ' + boot[2] if boot else 'нет данных'}")
print(f"Архивов: {len(st) - 1}; идёт синхронизация: {', '.join(busy) or 'нет'}; ошибки: {', '.join(errs) or 'нет'}")

"""Какие серверы-посредники Tailscale (DERP) недоступны с этого компьютера.

Живой случай 2026-10-15: из России (домашний интернет Алексея) не открываются серверы Tailscale в Токио (3 из 4),
Нюрнберге и Хельсинки. Машина в Корее выбирает «домашним» Токио — компьютер до неё не достаёт: Tailscale шлёт
пакеты на посредник машины, а прямая связь без посредника и не начинается («tx есть, rx 0»). Убрать такие
города можно только в настройках сети Tailscale (блок "derpMap" в Access controls) — это делает человек, мы даём
готовый блок и инструкцию. Убранные регионы пропадают из карты посредников — следующая проверка их уже не видит.

Проверяем, как ходит сам Tailscale: мимо VPN (на Mac — через физический интерфейс, IP_BOUND_IF). Через VPN
incy (режим TUN) проверка врёт: он сам отвечает на любое TCP-соединение, а TLS доходит до всех городов."""
import json
import math
import socket
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import paths

TIMEOUT = 3          # с на одно соединение
CACHE_SEC = 60       # итог помним минуту. Дольше нельзя: человек убрал город в настройках Tailscale
                      # и ждёт, что предупреждение погаснет. «Проверить снова» и возврат в окно
                      # спрашивают заново (fresh=True), не дожидаясь этой минуты.
POLICY_URL = "https://login.tailscale.com/admin/acls/file"
IP_BOUND_IF = 25     # macOS <netinet/in.h>
TESTS = False        # под автотестами — только если тест разрешил (иначе пошли бы в сеть)

_cache = None        # (время, итог)


def _derp_map():
    exe = paths.find("tailscale")
    if not exe:
        return None
    try:
        r = paths.run([exe, "debug", "derp-map"], paths.TAILSCALE_TIMEOUT, env=paths.tailscale_env())
        return json.loads(r.stdout or "null")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _iface():
    """Физический интерфейс Mac с адресом (en0…): через него ходит Tailscale и при включённом VPN."""
    if sys.platform != "darwin":
        return None
    for n in range(10):
        name = f"en{n}"
        try:
            if paths.run(["/usr/sbin/ipconfig", "getifaddr", name], 3).stdout.strip():
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _reach(node, iface):
    """TLS до сервера (рукопожатие с его именем) — две попытки."""
    for _ in range(2):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if iface:
                    s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, socket.if_nametoindex(iface))
                s.settimeout(TIMEOUT)
                s.connect((node["IPv4"], node.get("DERPPort") or 443))
                ssl.create_default_context().wrap_socket(s, server_hostname=node["HostName"]).close()
                return True
            finally:
                s.close()
        except (OSError, ValueError):
            pass
    return False


def blocked(dmap=None, reach=None, fresh=False):
    """[{id, code, name}] — регионы, где отвечает меньше половины серверов; [] — всё доступно;
    None — проверить нельзя (нет Tailscale или он не ответил).
    fresh — не смотреть в память минуты: человек нажал «Проверить снова» или вернулся в окно."""
    global _cache
    if dmap is None and reach is None:
        if "unittest" in sys.modules and not TESTS:
            return None
        if not fresh and _cache and time.time() - _cache[0] < CACHE_SEC:
            return _cache[1]
    dmap = dmap if dmap is not None else _derp_map()
    if not dmap or not dmap.get("Regions"):
        return None
    if reach is None:
        iface = _iface()
        reach = lambda n: _reach(n, iface)        # noqa: E731
    regions = []
    for rid, r in (dmap.get("Regions") or {}).items():
        nodes = [n for n in (r or {}).get("Nodes") or [] if n.get("IPv4") and n.get("HostName")]
        if nodes and not (r or {}).get("Avoid"):
            regions.append((int(rid), r, nodes))
    with ThreadPoolExecutor(32) as ex:
        res = {rid: ex.map(reach, nodes) for rid, r, nodes in regions}
        res = {rid: list(v) for rid, v in res.items()}
    out = [{"id": rid, "code": r.get("RegionCode") or str(rid), "name": r.get("RegionName") or str(rid)}
           for rid, r, nodes in sorted(regions, key=lambda x: x[0])
           if sum(res[rid]) < math.ceil(len(nodes) / 2)]
    _cache = (time.time(), out)
    return out


def policy_block(ids):
    """Блок для настроек сети Tailscale: убрать эти регионы у всех устройств сети."""
    lines = ",\n".join(f'\t\t\t"{i}": null' for i in sorted(ids))
    return '\t"derpMap": {\n\t\t"Regions": {\n' + lines + '\n\t\t}\n\t},'

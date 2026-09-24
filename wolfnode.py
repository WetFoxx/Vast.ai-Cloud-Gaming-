# wolfnode.py — связь с инстансом через Tailscale: найти узел, прочитать статус wolf,
# выполнить команду по Tailscale SSH. Используют play.py и stop.py.

import json
import subprocess

import requests

import paths

NODE = "vastai-gaming"   # имя узла в Tailscale (TAILSCALE_HOSTNAME в wolf-setup.sh)
WOLF_PORT = 8099         # страница статуса wolf на инстансе


def ts_node():
    """Найти узел NODE в Tailscale на Mac, который сейчас онлайн. Возвращает (True, IP, DNS-имя) или None.

    Узлов с именем vastai-gaming в сети может быть несколько — старые, от прошлых аренд
    (их DNS-имена vastai-gaming-1, -2...). Нам нужен тот, что онлайн.
    """
    peers = (paths.tailscale_status() or {}).get("Peer") or {}
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


SUNSHINE_UI_PORT = 47990   # веб-кабинет Sunshine (HTTPS): пароль, PIN для Moonlight


def links(ip, dns):
    """Адреса для подключения: {"moonlight": имя узла, "sunshine": кабинет, "status": страница wolf}."""
    host = dns or ip   # имя MagicDNS не меняется между арендами (если личность восстановилась), IP — может
    return {"moonlight": host,
            "sunshine": f"https://{host}:{SUNSHINE_UI_PORT}",
            "status": f"http://{host}:{WOLF_PORT}"}


def print_links(ip, dns):
    """То же для терминала."""
    li = links(ip, dns)
    print(f"""
  Moonlight — добавь ПК вручную по имени (не по IP):
      {li['moonlight']}
  Кабинет Sunshine — PIN для Moonlight, логин и пароль Sunshine:
      {li['sunshine']}
      (браузер предупредит о сертификате — это нормально: у Sunshine свой, самоподписанный)
  Статус синхронизации:
      {li['status']}
  Всё открывается только с устройств в твоей сети Tailscale.""")


def ssh_run(ip, command, timeout=180, on_notice=None, stdin_text=None):
    """Выполнить короткую команду по Tailscale SSH и вернуть (код, вывод).

    Если политика Tailscale требует подтверждения («Tailscale SSH requires an additional check»),
    Tailscale печатает ссылку — передаём её строки в on_notice(строка) сразу (по умолчанию — печать)
    и ждём, пока человек подтвердит вход в браузере.
    """
    if on_notice is None:
        def on_notice(text):
            print("  " + text, flush=True)
    import threading
    proc = ssh(ip, command, stdin_text)
    lines = []

    def read():
        for line in proc.stdout:
            if line.startswith("#"):   # сообщения самого Tailscale SSH — показать человеку
                on_notice(line.lstrip("# ").rstrip())
            elif line.startswith("Warning: Permanently added"):   # ssh про ключ новой машины — шум
                pass
            else:
                lines.append(line)
    t = threading.Thread(target=read, daemon=True)
    t.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return None, "не ответил за %d с" % timeout
    t.join(5)
    return proc.returncode, "".join(lines)


def ssh(ip, command, stdin_text=None):
    """Выполнить команду на инстансе как root через Tailscale SSH. Возвращает процесс (Popen).

    BatchMode — не задавать вопросов (пароль и т.п.). Подтверждение Tailscale SSH («check»)
    это не отменяет: ssh ждёт, пока человек откроет ссылку, — поэтому короткие команды
    запускаем через ssh_run, который эту ссылку показывает.
    accept-new — ключ хоста каждой новой машины принимается сам (машины каждый раз новые).
    """
    proc = subprocess.Popen(
        [paths.need("ssh"), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         "-o", "UserKnownHostsFile=/dev/null",
         "-o", "ConnectTimeout=20",
         # машина пропала (удалили, упала) — ssh заметит за ~1 минуту, а не будет ждать вечно
         "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
         f"root@{ip}", command],
        # LogLevel=ERROR не ставим: он прячет и баннер со ссылкой подтверждения Tailscale.
        # stdin закрыт — ssh не должен забирать ввод из терминала; stdin_text — передать команде
        # секрет не в её тексте (он виден в списке процессов), а во вход (пароль Sunshine)
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if stdin_text is not None:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
    return proc

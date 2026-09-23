# wolfnode.py — связь с инстансом через Tailscale: найти узел, прочитать статус wolf,
# выполнить команду по Tailscale SSH. Используют play.py и stop.py.

import json
import subprocess

import requests

NODE = "vastai-gaming"   # имя узла в Tailscale (TAILSCALE_HOSTNAME в wolf-setup.sh)
WOLF_PORT = 8099         # страница статуса wolf на инстансе


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


SUNSHINE_UI_PORT = 47990   # веб-кабинет Sunshine (HTTPS): пароль, PIN для Moonlight


def print_links(ip, dns):
    """Адреса для подключения: Moonlight, кабинет Sunshine, страница статуса wolf."""
    host = dns or ip   # имя MagicDNS не меняется между арендами (если личность восстановилась), IP — может
    print(f"""
  Moonlight — добавь ПК вручную по имени (не по IP):
      {host}
  Кабинет Sunshine — PIN для Moonlight, логин и пароль Sunshine:
      https://{host}:{SUNSHINE_UI_PORT}
      (браузер предупредит о сертификате — это нормально: у Sunshine свой, самоподписанный)
  Статус синхронизации:
      http://{host}:{WOLF_PORT}
  Всё открывается только с устройств в твоей сети Tailscale.""")


def ssh_run(ip, command, timeout=180):
    """Выполнить короткую команду по Tailscale SSH и вернуть (код, вывод).

    Если политика Tailscale требует подтверждения («Tailscale SSH requires an additional check»),
    Tailscale печатает ссылку — показываем её сразу и ждём, пока человек подтвердит вход в браузере.
    """
    import threading
    proc = ssh(ip, command)
    lines = []

    def read():
        for line in proc.stdout:
            if line.startswith("#"):   # сообщения самого Tailscale SSH — показать человеку
                print("  " + line.lstrip("# ").rstrip(), flush=True)
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


def ssh(ip, command):
    """Выполнить команду на инстансе как root через Tailscale SSH. Возвращает процесс (Popen).

    BatchMode — не задавать вопросов (пароль и т.п.). Подтверждение Tailscale SSH («check»)
    это не отменяет: ssh ждёт, пока человек откроет ссылку, — поэтому короткие команды
    запускаем через ssh_run, который эту ссылку показывает.
    accept-new — ключ хоста каждой новой машины принимается сам (машины каждый раз новые).
    """
    return subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         "-o", "UserKnownHostsFile=/dev/null",
         "-o", "ConnectTimeout=20", f"root@{ip}", command],
        # LogLevel=ERROR не ставим: он прячет и баннер со ссылкой подтверждения Tailscale.
        # stdin закрыт — ssh не должен забирать ввод из терминала
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

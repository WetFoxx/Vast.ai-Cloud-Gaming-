# wolfnode.py — связь с инстансом через Tailscale: найти узел, прочитать статус wolf,
# выполнить команду на машине. Используют play.py и stop.py.
#
# Команды (run): с wolf-setup.sh v3.15 — через страницу wolf (POST /cmd). Машина выполняет их только
# от устройства того же владельца в Tailscale и без Tailscale SSH, который в сетях по умолчанию
# раз в несколько часов просит подтвердить вход в браузере. Машина со скриптом старее — по SSH.

import json
import os
import shlex
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


class Rec(tuple):
    """Запись страницы /json: (время, состояние, текст), как раньше; .progress — ход архива (wolf-setup.sh v4.6):
    {"done", "total", "speed"} в байтах и байтах/с, пока он скачивается или выгружается, иначе None."""
    progress = None


def wolf_status(ip):
    """Статусы архивов со страницы /json на инстансе: {имя: Rec(время, состояние, текст)}."""
    try:
        r = requests.get(f"http://{ip}:{WOLF_PORT}/json", timeout=5)
        out = {}
        for x in r.json():
            rec = Rec((x["ts"], x["state"], x["text"]))
            pg = x.get("progress")
            if isinstance(pg, dict) and all(isinstance(pg.get(k), int) for k in ("done", "total", "speed")):
                rec.progress = {k: pg[k] for k in ("done", "total", "speed")}
            out[x["name"]] = rec
        return out
    except Exception:
        return None


# Команды, которые понимает страница wolf, и то же самое для старых машин — строкой для SSH
SHELL = {"ping": lambda a: "echo ok", "time": lambda a: "date +%s", "finish": lambda a: "wolf finish",
         "push-identity": lambda a: "(NOW=1 wolf push identity </dev/null >/dev/null 2>&1 &)",
         "forget": lambda a: f"wolf forget {shlex.quote(a[0])}",
         "sunshine-creds": lambda a: f"wolf sunshine-creds {shlex.quote(a[0])}",
         "steamlink-check": lambda a: "wolf steamlink-check" + (
             "" if not a else " " + " ".join(shlex.quote(x) for x in a)),
         "steamlink-pin": lambda a: f"wolf steamlink-pin {shlex.quote(a[0])}",
         "answer": lambda a: "wolf answer " + " ".join(shlex.quote(x) for x in a),
         "prog-run": lambda a: "wolf prog run " + " ".join(shlex.quote(x) for x in a),          # [v5.4]
         "prog-auto": lambda a: "wolf prog auto " + " ".join(shlex.quote(x) for x in a),
         "restore": lambda a: f"(wolf restore {shlex.quote(a[0])} </dev/null >/dev/null 2>&1 &)"}   # [v5.10] «Повторить»


def post_cmd(ip, cmd, args=(), stdin=None, timeout=120):
    """Команда через страницу wolf: (код, вывод) или None — такой страницы у машины нет (скрипт
    старее v3.15) или она недоступна; тогда вызывающий идёт по SSH."""
    from vastapi import VastgameError
    try:
        r = requests.post(f"http://{ip}:{WOLF_PORT}/cmd", json={"cmd": cmd, "args": list(args), "stdin": stdin},
                          timeout=(10, timeout))
    except requests.Timeout as e:
        if isinstance(e, requests.ConnectTimeout):
            return None
        return None, f"не ответил за {timeout} с"      # команда идёт — повторять по SSH нельзя
    except requests.RequestException:
        return None
    if r.status_code in (404, 405, 501):                # старая страница: POST не умеет
        return None
    if r.status_code == 403:
        raise VastgameError("Машина не приняла команду: это устройство другого владельца в Tailscale.",
                            key="machine.forbidden")
    try:
        d = r.json()
    except ValueError:
        return None, r.text[-300:]
    return (d.get("code"), d.get("out") or "") if r.status_code == 200 else (None, d.get("error") or "")


def run(ip, cmd, args=(), stdin=None, timeout=120, on_notice=None, ssh=None):
    """Команда на машине (cmd — из SHELL): страница wolf, а у старой машины — Tailscale SSH (ssh —
    функция вида ssh_run, по умолчанию она сама). Возвращает (код, вывод), как ssh_run."""
    r = post_cmd(ip, cmd, args, stdin, timeout)
    if r is not None:
        return r
    extra = {"stdin_text": stdin} if stdin else {}
    return (ssh or ssh_run)(ip, SHELL[cmd](list(args)), timeout=timeout, on_notice=on_notice, **extra)


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
         "-o", f"UserKnownHostsFile={os.devnull}",       # Mac — /dev/null, Windows — nul
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

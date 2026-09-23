# core.py — логика «Играть» и «Закончить» без привязки к терминалу или окну.
#
# Функции ничего не печатают и не спрашивают сами: всё общение — через объект ui.
# Терминал передаёт ConsoleUI (cliui.py), окно приложения — свой ui. Методы ui:
#   ui.say(text, level)          сообщение; level: "info", "detail", "ok", "warn", "error"
#   ui.stage(key, text)          смена этапа — по нему меняется значок статуса:
#                                  "vast" → "tailscale" → "restore" → "ready";
#                                  "shutdown" → "verify" → "deleted"
#   ui.archive(name, state, text) изменился архив на странице /json (state: ok, up, down, wait, err)
#   ui.confirm(question) -> bool  вопрос да/нет; True — только явное «да»
#   ui.tailscale(text)           строка от Tailscale SSH (ссылка подтверждения входа)
#
# Ошибки — VastgameError(сообщение): в терминале печатается и завершает программу,
# окно её перехватывает и показывает.

import re
import threading
import time

import market
from vastapi import TEMPLATE_NAME, VastgameError, call, list_instances, my_template
from wolfnode import NODE, links, ssh, ssh_run, ts_node, wolf_status

VAST_START_MIN = 30      # сколько ждать, пока Vast запустит машину
TAILSCALE_WARN_MIN = 10  # после стольких минут без узла — подсказать, где искать причину
TAILSCALE_MIN = 20       # сколько всего ждать узел в Tailscale
SHUTDOWN_MIN = 30        # сколько ждать wolf shutdown


# ------------------------------------------------------------------ подготовка аренды ---

def instances():
    """Мои инстансы (одновременно должен быть не больше одного)."""
    return list_instances()


def running_message(running):
    ids = ", ".join(str(i["id"]) for i in running)
    return f"У тебя уже есть инстанс ({ids}). Одновременно — только один: общие архивы и личность Tailscale."


def template():
    """Шаблон vastgame (создаёт мастер) или VastgameError."""
    tpl = my_template()
    if not tpl:
        raise VastgameError(f"Нет шаблона «{TEMPLATE_NAME}» — создай его: vastgame setup")
    return tpl


def template_disk(tpl):
    """Размер диска по умолчанию — из шаблона."""
    return int(tpl.get("recommended_disk_space") or 100)


def template_games(tpl):
    """SYNC_STEAM_GAMES из шаблона: "1" или "0"."""
    return "1" if re.search(r'-e\s+SYNC_STEAM_GAMES="?1', tpl.get("env") or "") else "0"


def offer_by_id(offer_id, disk):
    """Свежие данные машины с нашим диском. VastgameError, если она не подходит."""
    # Поле id в фильтре поиска не работает (молча находит 0), а ask_contract_id — работает
    found = call("POST", "bundles/", {
        "ask_contract_id": {"eq": offer_id}, "rentable": {"eq": True}, "type": "ondemand",
        "allocated_storage": disk, "limit": 1,
    }).get("offers", [])
    if not found:
        raise VastgameError(f"Машина {offer_id} уже недоступна — подбери другую: vastgame play")
    o = found[0]
    if not o.get("vms_enabled"):
        raise VastgameError(f"Машина {offer_id} не поддерживает KVM — шаблон на ней не запустится.")
    if o.get("disk_space", 0) < disk:
        raise VastgameError(f"На машине свободно {o['disk_space']:.0f} ГБ, а нужно {disk}.")
    return o


def bad_host_reason(o):
    """Почему этот хост в чёрном списке, или None."""
    b = market.bad_hosts().get(str(o.get("machine_id")))
    return b["reason"] if b else None


def rental_body(tpl, disk, games=None):
    """Тело запроса аренды. games — "1"/"0", чтобы перекрыть шаблон только для этой аренды."""
    body = {"client_id": "me", "template_hash_id": tpl["hash_id"], "disk": disk, "label": "vastgame"}
    if games is not None:
        # wolf-setup.sh читает SYNC_STEAM_GAMES при старте машины
        body["env"] = {"SYNC_STEAM_GAMES": games}
    return body


def balance():
    return call("GET", "users/current/").get("credit", 0)


# ------------------------------------------------------------------------- аренда ---

def rent(offer_id, body):
    """Арендовать. Возвращает (id инстанса, время начала). Деньги начинают списываться сразу."""
    t0 = time.time()
    r = call("PUT", f"asks/{offer_id}/", body)
    iid = r.get("new_contract")
    if not r.get("success") or not iid:
        raise VastgameError(f"Vast не арендовал машину: {r.get('msg') or r.get('error') or 'без объяснения'}")
    return iid, t0


def delete_instance(iid):
    """Удалить инстанс. (успех, сообщение Vast)."""
    r = call("DELETE", f"instances/{iid}/", {})
    return bool(r.get("success")), r.get("msg")


def _failed_start(iid, why, o, ui, bad_host):
    """Машина уже платная, но не запустилась: запомнить хост, предложить удалить, VastgameError."""
    ui.say(f"!!! {why}", "error")
    if bad_host:
        market.mark_bad(o.get("machine_id"), why)
        ui.say(f"Этот хост (machine {o.get('machine_id')}) больше не будет предлагаться.", "warn")
    ui.say(f"Инстанс {iid} арендован и тратит деньги.", "warn")
    if ui.confirm("Удалить его сейчас? На нём ещё ничего не сохранялось"):
        ok, msg = delete_instance(iid)
        ui.say("Удалён." if ok else f"Vast не удалил: {msg}", "ok" if ok else "error")
    else:
        ui.say("Не удаляю. Удалить можно в консоли Vast (Instances → корзина).", "warn")
    raise VastgameError("Попробовать другую машину: vastgame play" if bad_host else "Запуск не удался.")


def wait_ready(iid, t0, o, ui):
    """Дождаться, пока машина готова к игре: Vast → Tailscale → восстановление из Drive.

    Возвращает {"ip", "dns", "links", "boot", "identity", "minutes"}.
    """
    # ---- Vast запускает машину
    ui.stage("vast", f"Арендовано: инстанс {iid}. Жду запуска (обычно 3–15 минут: машина качает образ).")
    last, errors = None, 0
    deadline = time.time() + VAST_START_MIN * 60
    while True:
        # Разовый сбой API не должен обрывать ожидание: машина уже платная, следим дальше
        try:
            inst = call("GET", f"instances/{iid}/", params={"owner": "me"}).get("instances") or {}
        except VastgameError as e:
            ui.say(f"  (Vast не ответил: {e} — пробую ещё раз)", "detail")
            inst = {"actual_status": last}
        st = inst.get("actual_status")
        if st != last:
            ui.say(f"  Vast: {st or 'создаётся'}  {(inst.get('status_msg') or '').strip()[:80]}", "detail")
            last = st
        if st == "running":
            # Диагностика: что реально попало на машину. Только ИМЕНА переменных — значения (ключи)
            # не показываем. Порты (-p ...) тоже лежат в extra_env — их пропускаем. Секретные
            # переменные (ключи из аккаунта, OPEN_BUTTON_TOKEN) API не показывает — это нормально.
            names = sorted(e[0] for e in inst.get("extra_env") or [] if e and not e[0].startswith("-"))
            ui.say(f"  Переменные на машине (кроме секретных): {', '.join(names) or 'НЕТ'}", "detail")
            for k in sorted(inst):
                if "onstart" in k or "template" in k:
                    v = inst[k]
                    ui.say(f"  {k}: " + (f"{len(v)} символов" if isinstance(v, str) and len(v) > 40 else repr(v)),
                           "detail")
            ip_pub = inst.get("public_ipaddr") or "?"
            break
        # Из этих состояний машина уже не дойдёт до running (см. документацию Vast)
        if st in ("exited", "offline", "unknown"):
            _failed_start(iid, f"Машина перешла в состояние «{st}» и не запустится.", o, ui, bad_host=True)
        # Хост не смог создать машину: «Error response from daemon ...» в status_msg.
        # Ошибку, повторившуюся две проверки подряд (~30 с), считаем окончательной.
        errors = errors + 1 if "error" in (inst.get("status_msg") or "").lower() else 0
        if errors >= 2:
            _failed_start(iid, "Хост не смог запустить машину: " + inst["status_msg"].strip()[:200],
                          o, ui, bad_host=True)
        if time.time() > deadline:
            _failed_start(iid, f"Машина не запустилась за {VAST_START_MIN} минут.", o, ui, bad_host=True)
        time.sleep(15)

    # ---- Узел появляется в Tailscale (wolf-setup.sh поднимает его с восстановленной личностью)
    ui.stage("tailscale", "Машина запущена. Жду узел vastai-gaming в Tailscale (обычно 5–7 минут).")
    started = time.time()
    warned = False
    while True:
        if not warned and time.time() - started > TAILSCALE_WARN_MIN * 60:
            warned = True
            ui.say(f"Узла нет уже {TAILSCALE_WARN_MIN} минут — это дольше обычного. "
                   f"Публичный IP машины: {ip_pub}\n"
                   "Посмотри на машине (консоль Vast → Instances → Open → терминал):\n"
                   "  tail -n 30 /var/log/wolf-setup.log     # установка\n"
                   "  tail -n 30 /var/log/wolf.log           # восстановление из Drive\n"
                   f"Жду ещё до {TAILSCALE_MIN} минут.", "warn")
        node = ts_node()
        if node and wolf_status(node[1]) is not None:
            _, ip, dns = node
            break
        if time.time() - started > TAILSCALE_MIN * 60:
            raise VastgameError(f"!!! Узел не появился в Tailscale за {TAILSCALE_MIN} минут. "
                                "Инстанс работает и тратит деньги.\n"
                                "Посмотри лог в консоли Vast (Open → терминал): /var/log/wolf-setup.log")
        time.sleep(15)
    ui.say(f"Tailscale: {dns or ip} ({ip}) онлайн.", "ok")

    # ---- wolf восстанавливает всё из Google Drive
    ui.stage("restore", "Восстановление из Google Drive:")
    seen = {}
    while True:
        s = wolf_status(ip) or {}
        for name, (ts, state, text) in sorted(s.items()):
            if ts >= t0 - 60 and seen.get(name) != (state, text):
                ui.archive(name, state, text)
                seen[name] = (state, text)
        boot = s.get("boot")
        if boot and boot[0] >= t0 - 60 and boot[1] in ("ok", "err"):
            break
        time.sleep(10)

    ident = (wolf_status(ip) or {}).get("identity")
    result = {"ip": ip, "dns": dns, "links": links(ip, dns), "boot": boot, "identity": ident,
              "minutes": int((time.time() - t0) / 60)}
    ui.stage("ready", f"Готово за {result['minutes']} мин.")
    return result


def identity_notes(result):
    """Предупреждения про личность Tailscale по итогам запуска: [(level, text)]."""
    ident, dns = result["identity"], result["dns"]
    short = dns.split(".")[0] if dns else ""
    # Смотрим на статус identity, а не на имя: номер в имени (vastai-gaming-5) бывает и у
    # восстановленного узла — если прежние имена заняты старыми узлами в сети
    if not ident or ident[1] == "err" or "нет в облаке" in ident[2]:
        return [("warn", f"!!! Личность Tailscale не восстановилась ({ident[2] if ident else 'нет данных'}): "
                         "это новый узел, и в Moonlight его придётся добавить заново. "
                         "Старый узел можно удалить в админке Tailscale.")]
    if short != NODE:
        return [("info", f"Узел прежний, но имя с номером ({short}): имя {NODE} занято старым узлом в сети. "
                         "Удали лишние офлайн-узлы vastai-gaming в админке Tailscale — "
                         "имя станет короче со следующей аренды.")]
    return []


# -------------------------------------------------------------------------- статус ---

# Состояния для главного экрана и значка
NONE, STARTING, READY, SYNCING, ERROR = "none", "starting", "ready", "syncing", "error"


def status():
    """Что сейчас с машиной — для главного экрана и значка. Только читает, ничего не меняет.

    {"state": none|starting|ready|syncing|error,
     "stage": vast|tailscale|restore (пока starting), "text": что показать человеку,
     "machine": {"id", "gpu", "country", "price"} или None,
     "links": адреса (когда узел в сети), "archives": {имя: (время, состояние, текст)},
     "busy": [архивы в работе], "errors": [архивы с ошибкой]}
    Выключение (wolf shutdown) снаружи не видно — его показывает окно, которое его запустило.
    """
    out = {"state": NONE, "stage": None, "text": "Машины нет", "machine": None,
           "links": None, "archives": {}, "busy": [], "errors": []}
    inst = list_instances()
    if not inst:
        return out
    if len(inst) > 1:
        return dict(out, state=ERROR, text=f"Машин несколько ({len(inst)}) — разберись в консоли Vast")
    i = inst[0]
    out["machine"] = {"id": i["id"], "gpu": i.get("gpu_name") or "?", "country": market.country(i),
                      "price": i.get("dph_total") or 0}
    st, msg = i.get("actual_status"), (i.get("status_msg") or "").strip()
    if st in ("exited", "offline", "unknown") or (st != "running" and "error" in msg.lower()):
        return dict(out, state=ERROR, text=f"Vast: {st or '?'} {msg[:120]}".strip())
    if st != "running":
        return dict(out, state=STARTING, stage="vast", text="Vast запускает машину")
    node = ts_node()
    s = wolf_status(node[1]) if node else None
    if s is None:
        return dict(out, state=STARTING, stage="tailscale", text="Машина запущена, жду её в Tailscale")
    _, ip, dns = node
    out.update(links=links(ip, dns), archives=s)
    busy = sorted(n for n, v in s.items() if n != "boot" and v[1] in ("up", "down"))
    errors = sorted(n for n, v in s.items() if n != "boot" and v[1] == "err")
    out.update(busy=busy, errors=errors)
    boot = s.get("boot")
    if boot and boot[1] == "err":
        return dict(out, state=ERROR, text=f"Восстановление с ошибками: {boot[2]}")
    if not boot or boot[1] != "ok":
        done = sum(1 for n, v in s.items() if n != "boot" and v[1] == "ok")
        return dict(out, state=STARTING, stage="restore",
                    text=f"Восстановление из Google Drive: готово {done}, в работе {len(busy)}")
    if errors:
        return dict(out, state=ERROR, text=f"Ошибка синхронизации: {', '.join(errors)}")
    if busy:
        return dict(out, state=SYNCING, text=f"Синхронизация: {', '.join(busy)}")
    return dict(out, state=READY, text="Готова к игре")


# ------------------------------------------------------------------------ остановка ---

def _find_machine():
    """(id инстанса, ip, dns) единственной машины или VastgameError. Ничего не меняет."""
    inst = list_instances()
    if not inst:
        raise VastgameError("Инстансов нет — останавливать нечего.")
    if len(inst) > 1:
        raise VastgameError(f"Инстансов несколько ({', '.join(str(i['id']) for i in inst)}) — "
                            "разберись в консоли Vast.")
    iid = inst[0]["id"]
    node = ts_node()
    if not node:
        raise VastgameError(f"Инстанс {iid} есть, но узла vastai-gaming в Tailscale нет — выгрузить нечем.\n"
                            "НЕ удаляю. Проверь машину через консоль Vast (Open → терминал).")
    _, ip, dns = node
    return iid, ip, dns


def stop(ui, dry_run=False):
    """Выгрузить всё с машины в Google Drive, проверить и только потом (после «да») удалить её.

    Возвращает {"iid", "uploaded", "deleted"} или, при dry_run, {"iid", "archives"}.
    Выгрузка не подтвердилась — VastgameError, машина НЕ удаляется.
    """
    iid, ip, dns = _find_machine()
    ui.say(f"Инстанс {iid}, узел {dns or ip}")
    before = wolf_status(ip)
    if before is None:
        raise VastgameError("Страница статуса wolf не отвечает — НЕ удаляю. Проверь машину.")
    boot = before.get("boot")
    if not boot or boot[1] != "ok":
        ui.say(f"!!! Загрузка ещё не закончена или с ошибкой: boot = {boot[1] if boot else 'нет'} "
               f"{boot[2] if boot else ''}", "warn")

    # Проверка SSH безвредной командой — до того, как что-то запускать.
    # Если Tailscale попросит подтвердить вход — ссылка придёт в ui.tailscale; ждём до 3 минут
    ui.say("Проверяю Tailscale SSH (если появится ссылка — открой её и подтверди вход)...")
    code, out = ssh_run(ip, "echo ok", on_notice=ui.tailscale)
    if code != 0 or out.strip() != "ok":
        raise VastgameError(f"Tailscale SSH не работает (код {code}): {out.strip()[:200]}\n"
                            "НЕ удаляю. Выгрузи вручную на машине: sudo wolf shutdown")
    ui.say("Tailscale SSH: работает.", "ok")
    if dry_run:
        return {"iid": iid, "archives": before}

    # ---- wolf shutdown; время берём с машины, чтобы сравнивать с ts в /json по одним часам
    code, out = ssh_run(ip, "date +%s", timeout=60, on_notice=ui.tailscale)
    if code != 0 or not out.strip().isdigit():
        raise VastgameError("Не удалось узнать время на машине — НЕ запускаю выгрузку.")
    t0 = int(out.strip()) - 2
    ui.stage("shutdown", "Закрываю Steam и выгружаю всё в Google Drive (может занять несколько минут)...")
    proc = ssh(ip, "wolf shutdown")
    # Вывод wolf читаем параллельно: если ждать конца, канал может переполниться, и ssh встанет
    output = []
    reader = threading.Thread(target=lambda: output.extend(proc.stdout), daemon=True)
    reader.start()
    seen = {}
    deadline = time.time() + SHUTDOWN_MIN * 60
    last_check = time.time()
    while True:
        # Раз в минуту — жив ли инстанс: его могли удалить в консоли Vast посреди выгрузки
        if time.time() - last_check > 60:
            last_check = time.time()
            if iid not in [i["id"] for i in list_instances()]:
                proc.kill()
                raise VastgameError(f"!!! Инстанс {iid} удалён во время выгрузки (не этой командой).\n"
                                    "Что успело выгрузиться — выше; остальное не дошло до Drive.")
        s = wolf_status(ip) or {}
        for name, (ts, state, text) in sorted(s.items()):
            if ts >= t0 and seen.get(name) != (state, text):
                ui.archive(name, state, text)
                seen[name] = (state, text)
        if proc.poll() is not None:
            break
        if time.time() > deadline:
            proc.kill()
            raise VastgameError(f"!!! wolf shutdown не закончился за {SHUTDOWN_MIN} минут — НЕ удаляю. "
                                "Посмотри /var/log/wolf.log.")
        time.sleep(5)
    reader.join(5)
    if proc.returncode != 0:
        raise VastgameError(f"!!! SSH/wolf shutdown завершился с кодом {proc.returncode}: "
                            f"{''.join(output).strip()[-300:]}\nНЕ удаляю.")

    # ---- Проверка по /json: wolf shutdown всегда отвечает 0, верим только статусам архивов.
    # Учитываем записи новее запуска shutdown: старые ошибки живут в /json до перезагрузки
    ui.stage("verify", "Проверяю выгрузку...")
    after = wolf_status(ip)
    if after is None:
        raise VastgameError("!!! Страница статуса не отвечает после выгрузки — НЕ удаляю.")
    fresh = {n: v for n, v in after.items() if v[0] >= t0 and n != "boot"}
    bad = {n: v for n, v in fresh.items() if v[1] in ("up", "down", "err")}
    old_err = {n: v for n, v in after.items() if v[0] < t0 and v[1] == "err"}
    uploaded = sorted(n for n, v in fresh.items() if v[1] == "ok")
    ui.say(f"Выгружено в этот раз: {len(uploaded)} архив(ов)"
           + (f": {', '.join(uploaded)}" if uploaded else " (изменений не было — всё уже в облаке)"))
    if old_err:
        ui.say("Старые ошибки (до shutdown, к этой выгрузке не относятся):\n"
               + "\n".join(f"  {n}: {tx}" for n, (_, _, tx) in sorted(old_err.items())), "detail")
    if bad:
        raise VastgameError("!!! Выгрузка НЕ подтверждена:\n"
                            + "\n".join(f"  {n:<28} {st:<5} {tx}" for n, (_, st, tx) in sorted(bad.items()))
                            + "\nНЕ удаляю инстанс. Разберись на машине (/var/log/wolf.log), "
                              "затем запусти vastgame stop снова.")

    # ---- Удаление — только после «да»
    ui.say("Всё выгружено.", "ok")
    if not ui.confirm(f"Удалить инстанс {iid}? Диск машины будет стёрт"):
        raise VastgameError("Не удаляю. Инстанс продолжает работать и тратить деньги.")
    ok, msg = delete_instance(iid)
    if not ok:
        raise VastgameError(f"Vast не удалил инстанс: {msg}")
    time.sleep(5)
    gone = iid not in [i["id"] for i in list_instances()]
    ui.stage("deleted", f"Инстанс {iid} удалён." if gone
             else f"Vast принял удаление, но {iid} ещё в списке — проверь через минуту.")
    return {"iid": iid, "uploaded": uploaded, "deleted": gone}

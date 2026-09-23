# core.py — логика «Играть» и «Закончить» без привязки к терминалу или окну.
#
# Функции ничего не печатают и не спрашивают сами: всё общение — через объект ui.
# Терминал передаёт ConsoleUI (cliui.py), окно приложения — свой ui. Методы ui:
#   ui.say(text, level, key=None, **params)
#                                сообщение; level: "info", "detail", "ok", "warn", "error";
#                                key/params — для перевода в окне (i18n.py), text — русский (терминал)
#   ui.stage(key, text)          смена этапа — по нему меняется значок статуса:
#                                  "vast" → "tailscale" → "restore" → "ready";
#                                  "shutdown" → "verify" → "deleted"
#   ui.archive(name, state, text) изменился архив на странице /json (state: ok, up, down, wait, err)
#   ui.confirm(question, key=None, **params) -> bool  вопрос да/нет; True — только явное «да»
#   ui.tailscale(text)           строка от Tailscale SSH (ссылка подтверждения входа)
#
# Ошибки — VastgameError(сообщение, key=..., **params): в терминале печатается и завершает
# программу, окно её перехватывает и показывает перевод по ключу.

import re
import threading
import time

import history
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


def require_no_machine():
    """Перед арендой: машин быть не должно (одновременно — только одна). Иначе VastgameError."""
    running = list_instances()
    if running:
        raise VastgameError(running_message(running), key="core.running",
                            ids=", ".join(str(i["id"]) for i in running))


def template():
    """Шаблон vastgame (создаёт мастер) или VastgameError."""
    tpl = my_template()
    if not tpl:
        raise VastgameError(f"Нет шаблона «{TEMPLATE_NAME}» — создай его: vastgame setup",
                            key="core.no_template", name=TEMPLATE_NAME)
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
        raise VastgameError(f"Машина {offer_id} уже недоступна — подбери другую: vastgame play",
                            key="core.offer_gone", id=offer_id)
    o = found[0]
    if not o.get("vms_enabled"):
        raise VastgameError(f"Машина {offer_id} не поддерживает KVM — шаблон на ней не запустится.",
                            key="core.offer_no_kvm", id=offer_id)
    if o.get("disk_space", 0) < disk:
        raise VastgameError(f"На машине свободно {o['disk_space']:.0f} ГБ, а нужно {disk}.",
                            key="core.offer_disk", free=round(o["disk_space"]), need=disk)
    return o


def bad_host_reason(o):
    """Почему этот хост в чёрном списке, или None."""
    b = market.bad_hosts().get(str(o.get("machine_id")))
    return b["reason"] if b else None


# Что синхронизировать с облаком (wolf-setup.sh v3.9 читает эти переменные при старте машины)
SYNC_VARS = {"steam_games": "SYNC_STEAM_GAMES", "other_games": "SYNC_OTHER_GAMES", "saves": "SYNC_SAVES"}
SYNC_DEFAULTS = {"steam_games": False, "other_games": True, "saves": True}


def rental_body(tpl, disk, games=None, sync=None):
    """Тело запроса аренды. Переменные ниже перекрывают шаблон только для этой аренды:
    games — "1"/"0" для SYNC_STEAM_GAMES (терминал: --games/--no-games);
    sync — {"steam_games": bool, "other_games": bool, "saves": bool} (тумблеры в окне)."""
    body = {"client_id": "me", "template_hash_id": tpl["hash_id"], "disk": disk, "label": "vastgame"}
    env = {}
    if games is not None:
        env["SYNC_STEAM_GAMES"] = games
    for k, var in SYNC_VARS.items():
        if sync and k in sync:
            env[var] = "1" if sync[k] else "0"
    if env:
        body["env"] = env
    return body


def balance():
    return call("GET", "users/current/").get("credit", 0)


# ------------------------------------------------------------------------- аренда ---

def rent(offer_id, body, offer=None):
    """Арендовать. Возвращает (id инстанса, время начала). Деньги начинают списываться сразу.
    offer — данные машины (видеокарта, страна, цена) для истории сессий."""
    t0 = time.time()
    r = call("PUT", f"asks/{offer_id}/", body)
    iid = r.get("new_contract")
    if not r.get("success") or not iid:
        why = r.get("msg") or r.get("error") or ""
        raise VastgameError(f"Vast не арендовал машину: {why or 'без объяснения'}", key="core.rent_failed", msg=why)
    history.started(iid, offer or {}, t0)
    return iid, t0


def delete_instance(iid):
    """Удалить инстанс. (успех, сообщение Vast)."""
    r = call("DELETE", f"instances/{iid}/", {})
    if r.get("success"):
        history.ended(iid)
    return bool(r.get("success")), r.get("msg")


def _failed_start(iid, why, o, ui, bad_host, key, **params):
    """Машина уже платная, но не запустилась: запомнить хост, предложить удалить, VastgameError.
    why — причина по-русски (терминал и чёрный список), key/params — она же для перевода в окне."""
    ui.say(f"!!! {why}", "error", key=key, **params)
    if bad_host:
        market.mark_bad(o.get("machine_id"), why)
        ui.say(f"Этот хост (machine {o.get('machine_id')}) больше не будет предлагаться.", "warn",
               key="core.blacklisted", machine=o.get("machine_id"))
    ui.say(f"Инстанс {iid} арендован и тратит деньги.", "warn", key="core.costs", id=iid)
    if ui.confirm("Удалить его сейчас? На нём ещё ничего не сохранялось", key="core.ask_delete_unused"):
        ok, msg = delete_instance(iid)
        if ok:
            ui.say("Удалён.", "ok", key="core.deleted")
        else:
            ui.say(f"Vast не удалил: {msg}", "error", key="core.delete_failed", msg=msg or "")
    else:
        ui.say("Не удаляю. Удалить можно в консоли Vast (Instances → корзина).", "warn", key="core.not_deleting")
    if bad_host:
        raise VastgameError("Попробовать другую машину: vastgame play", key="core.try_another")
    raise VastgameError("Запуск не удался.", key="core.start_failed")


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
            ui.say(f"  (Vast не ответил: {e} — пробую ещё раз)", "detail", key="core.vast_retry", error=str(e))
            inst = {"actual_status": last}
        st = inst.get("actual_status")
        if st != last:
            vmsg = (inst.get("status_msg") or "").strip()[:80]
            ui.say(f"  Vast: {st or 'создаётся'}  {vmsg}", "detail", key="core.vast_status", status=st or "…", msg=vmsg)
            last = st
        if st == "running":
            # Диагностика: что реально попало на машину. Только ИМЕНА переменных — значения (ключи)
            # не показываем. Порты (-p ...) тоже лежат в extra_env — их пропускаем. Секретные
            # переменные (ключи из аккаунта, OPEN_BUTTON_TOKEN) API не показывает — это нормально.
            names = sorted(e[0] for e in inst.get("extra_env") or [] if e and not e[0].startswith("-"))
            ui.say(f"  Переменные на машине (кроме секретных): {', '.join(names) or 'НЕТ'}", "detail",
                   key="core.env_names", names=", ".join(names) or "—")
            for k in sorted(inst):
                if "onstart" in k or "template" in k:
                    v = inst[k]
                    ui.say(f"  {k}: " + (f"{len(v)} символов" if isinstance(v, str) and len(v) > 40 else repr(v)),
                           "detail")
            ip_pub = inst.get("public_ipaddr") or "?"
            break
        # Из этих состояний машина уже не дойдёт до running (см. документацию Vast)
        if st in ("exited", "offline", "unknown"):
            _failed_start(iid, f"Машина перешла в состояние «{st}» и не запустится.", o, ui, bad_host=True,
                          key="core.fail_state", state=st)
        # Хост не смог создать машину: «Error response from daemon ...» в status_msg.
        # Ошибку, повторившуюся две проверки подряд (~30 с), считаем окончательной.
        errors = errors + 1 if "error" in (inst.get("status_msg") or "").lower() else 0
        if errors >= 2:
            hmsg = inst["status_msg"].strip()[:200]
            _failed_start(iid, "Хост не смог запустить машину: " + hmsg, o, ui, bad_host=True,
                          key="core.fail_host", msg=hmsg)
        if time.time() > deadline:
            _failed_start(iid, f"Машина не запустилась за {VAST_START_MIN} минут.", o, ui, bad_host=True,
                          key="core.fail_timeout", min=VAST_START_MIN)
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
                   f"Жду ещё до {TAILSCALE_MIN} минут.", "warn",
                   key="core.tailscale_slow", min=TAILSCALE_WARN_MIN, ip=ip_pub, total=TAILSCALE_MIN)
        node = ts_node()
        if node and wolf_status(node[1]) is not None:
            _, ip, dns = node
            break
        if time.time() - started > TAILSCALE_MIN * 60:
            raise VastgameError(f"!!! Узел не появился в Tailscale за {TAILSCALE_MIN} минут. "
                                "Инстанс работает и тратит деньги.\n"
                                "Посмотри лог в консоли Vast (Open → терминал): /var/log/wolf-setup.log",
                                key="core.tailscale_timeout", min=TAILSCALE_MIN)
        time.sleep(15)
    ui.say(f"Tailscale: {dns or ip} ({ip}) онлайн.", "ok", key="core.tailscale_online", name=dns or ip, ip=ip)

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
    """Предупреждения про личность Tailscale по итогам запуска: [(level, text, key, params)]."""
    ident, dns = result["identity"], result["dns"]
    short = dns.split(".")[0] if dns else ""
    # Смотрим на статус identity, а не на имя: номер в имени (vastai-gaming-5) бывает и у
    # восстановленного узла — если прежние имена заняты старыми узлами в сети
    if not ident or ident[1] == "err" or "нет в облаке" in ident[2]:
        why = ident[2] if ident else "нет данных"
        return [("warn", f"!!! Личность Tailscale не восстановилась ({why}): "
                         "это новый узел, и в Moonlight его придётся добавить заново. "
                         "Старый узел можно удалить в админке Tailscale.", "core.identity_lost", {"text": why})]
    if short != NODE:
        return [("info", f"Узел прежний, но имя с номером ({short}): имя {NODE} занято старым узлом в сети. "
                         "Удали лишние офлайн-узлы vastai-gaming в админке Tailscale — "
                         "имя станет короче со следующей аренды.", "core.identity_renamed",
                 {"short": short, "node": NODE})]
    return []


# -------------------------------------------------------------------------- статус ---

# Состояния для главного экрана и значка
NONE, STARTING, READY, SYNCING, ERROR = "none", "starting", "ready", "syncing", "error"


def status():
    """Что сейчас с машиной — для главного экрана и значка. Только читает, ничего не меняет.

    {"state": none|starting|ready|syncing|error       — цвет значка,
     "kind": none|many|vast_error|vast|tailscale|boot_error|restore|sync_error|syncing|ready
                                                       — что именно; окно по нему собирает фразу,
     "stage": vast|tailscale|restore (пока starting),
     "text": то же по-русски (для терминала),
     "machine": {"id", "gpu", "country", "price"} или None,
     "links": адреса (когда узел в сети), "archives": {имя: (время, состояние, текст)},
     "busy": [архивы в работе], "errors": [архивы с ошибкой], "done": сколько восстановлено,
     "count": машин (для kind=many), "vast_status", "detail": исходный текст (Vast или wolf)}
    Выключение (wolf shutdown) снаружи не видно — его показывает окно, которое его запустило.
    """
    out = {"state": NONE, "kind": "none", "stage": None, "text": "Машины нет", "machine": None,
           "links": None, "archives": {}, "busy": [], "errors": [], "done": 0,
           "count": 0, "vast_status": None, "detail": ""}
    inst = list_instances()
    out["count"] = len(inst)
    if not inst:
        return out
    if len(inst) > 1:
        return dict(out, state=ERROR, kind="many", text=f"Машин несколько ({len(inst)}) — разберись в консоли Vast")
    i = inst[0]
    out["machine"] = {"id": i["id"], "gpu": i.get("gpu_name") or "?", "country": market.country(i),
                      "price": i.get("dph_total") or 0}
    st, msg = i.get("actual_status"), (i.get("status_msg") or "").strip()
    out.update(vast_status=st, detail=msg[:200])
    if st in ("exited", "offline", "unknown") or (st != "running" and "error" in msg.lower()):
        return dict(out, state=ERROR, kind="vast_error", text=f"Vast: {st or '?'} {msg[:120]}".strip())
    if st != "running":
        return dict(out, state=STARTING, kind="vast", stage="vast", text="Vast запускает машину")
    node = ts_node()
    s = wolf_status(node[1]) if node else None
    if s is None:
        return dict(out, state=STARTING, kind="tailscale", stage="tailscale",
                    text="Машина запущена, жду её в Tailscale")
    _, ip, dns = node
    busy = sorted(n for n, v in s.items() if n != "boot" and v[1] in ("up", "down"))
    errors = sorted(n for n, v in s.items() if n != "boot" and v[1] == "err")
    done = sum(1 for n, v in s.items() if n != "boot" and v[1] == "ok")
    out.update(links=links(ip, dns), archives=s, busy=busy, errors=errors, done=done, detail="")
    boot = s.get("boot")
    if boot and boot[1] == "err":
        return dict(out, state=ERROR, kind="boot_error", detail=boot[2],
                    text=f"Восстановление с ошибками: {boot[2]}")
    if not boot or boot[1] != "ok":
        return dict(out, state=STARTING, kind="restore", stage="restore",
                    text=f"Восстановление из Google Drive: готово {done}, в работе {len(busy)}")
    if errors:
        return dict(out, state=ERROR, kind="sync_error", text=f"Ошибка синхронизации: {', '.join(errors)}")
    if busy:
        return dict(out, state=SYNCING, kind="syncing", text=f"Синхронизация: {', '.join(busy)}")
    return dict(out, state=READY, kind="ready", text="Готова к игре")


# ------------------------------------------------------------------------ остановка ---

def _find_machine():
    """(id инстанса, ip, dns) единственной машины или VastgameError. Ничего не меняет."""
    inst = list_instances()
    if not inst:
        raise VastgameError("Инстансов нет — останавливать нечего.", key="core.no_instances")
    if len(inst) > 1:
        ids = ", ".join(str(i["id"]) for i in inst)
        raise VastgameError(f"Инстансов несколько ({ids}) — разберись в консоли Vast.",
                            key="core.many_instances", ids=ids)
    iid = inst[0]["id"]
    node = ts_node()
    if not node:
        raise VastgameError(f"Инстанс {iid} есть, но узла vastai-gaming в Tailscale нет — выгрузить нечем.\n"
                            "НЕ удаляю. Проверь машину через консоль Vast (Open → терминал).",
                            key="core.no_node", id=iid)
    _, ip, dns = node
    return iid, ip, dns


def stop(ui, dry_run=False):
    """Выгрузить всё с машины в Google Drive, проверить и только потом (после «да») удалить её.

    Возвращает {"iid", "uploaded", "deleted"} или, при dry_run, {"iid", "archives"}.
    Выгрузка не подтвердилась — VastgameError, машина НЕ удаляется.
    """
    iid, ip, dns = _find_machine()
    ui.say(f"Инстанс {iid}, узел {dns or ip}", key="core.stop_machine", id=iid, name=dns or ip)
    before = wolf_status(ip)
    if before is None:
        raise VastgameError("Страница статуса wolf не отвечает — НЕ удаляю. Проверь машину.",
                            key="core.no_status_page")
    boot = before.get("boot")
    if not boot or boot[1] != "ok":
        ui.say(f"!!! Загрузка ещё не закончена или с ошибкой: boot = {boot[1] if boot else 'нет'} "
               f"{boot[2] if boot else ''}", "warn", key="core.boot_not_ok",
               state=boot[1] if boot else "—", text=boot[2] if boot else "")

    # Проверка SSH безвредной командой — до того, как что-то запускать.
    # Если Tailscale попросит подтвердить вход — ссылка придёт в ui.tailscale; ждём до 3 минут
    ui.say("Проверяю Tailscale SSH (если появится ссылка — открой её и подтверди вход)...", key="core.ssh_check")
    code, out = ssh_run(ip, "echo ok", on_notice=ui.tailscale)
    if code != 0 or out.strip() != "ok":
        raise VastgameError(f"Tailscale SSH не работает (код {code}): {out.strip()[:200]}\n"
                            "НЕ удаляю. Выгрузи вручную на машине: sudo wolf shutdown",
                            key="core.ssh_failed", code=code, out=out.strip()[:200])
    ui.say("Tailscale SSH: работает.", "ok", key="core.ssh_ok")
    if dry_run:
        return {"iid": iid, "archives": before}

    # ---- wolf shutdown; время берём с машины, чтобы сравнивать с ts в /json по одним часам
    code, out = ssh_run(ip, "date +%s", timeout=60, on_notice=ui.tailscale)
    if code != 0 or not out.strip().isdigit():
        raise VastgameError("Не удалось узнать время на машине — НЕ запускаю выгрузку.", key="core.no_clock")
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
                                    "Что успело выгрузиться — выше; остальное не дошло до Drive.",
                                    key="core.deleted_during", id=iid)
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
                                "Посмотри /var/log/wolf.log.", key="core.shutdown_timeout", min=SHUTDOWN_MIN)
        time.sleep(5)
    reader.join(5)
    if proc.returncode != 0:
        tail = "".join(output).strip()[-300:]
        raise VastgameError(f"!!! SSH/wolf shutdown завершился с кодом {proc.returncode}: {tail}\nНЕ удаляю.",
                            key="core.shutdown_failed", code=proc.returncode, out=tail)

    # ---- Проверка по /json: wolf shutdown всегда отвечает 0, верим только статусам архивов.
    # Учитываем записи новее запуска shutdown: старые ошибки живут в /json до перезагрузки
    ui.stage("verify", "Проверяю выгрузку...")
    after = wolf_status(ip)
    if after is None:
        raise VastgameError("!!! Страница статуса не отвечает после выгрузки — НЕ удаляю.",
                            key="core.no_status_after")
    fresh = {n: v for n, v in after.items() if v[0] >= t0 and n != "boot"}
    bad = {n: v for n, v in fresh.items() if v[1] in ("up", "down", "err")}
    old_err = {n: v for n, v in after.items() if v[0] < t0 and v[1] == "err"}
    uploaded = sorted(n for n, v in fresh.items() if v[1] == "ok")
    if uploaded:
        ui.say(f"Выгружено в этот раз: {len(uploaded)} архив(ов): {', '.join(uploaded)}",
               key="core.uploaded", count=len(uploaded), names=uploaded)
    else:
        ui.say("Выгружено в этот раз: 0 архив(ов) (изменений не было — всё уже в облаке)", key="core.uploaded_none")
    if old_err:
        ui.say("Старые ошибки (до shutdown, к этой выгрузке не относятся):\n"
               + "\n".join(f"  {n}: {tx}" for n, (_, _, tx) in sorted(old_err.items())), "detail",
               key="core.old_errors", names=sorted(old_err))
    if bad:
        raise VastgameError("!!! Выгрузка НЕ подтверждена:\n"
                            + "\n".join(f"  {n:<28} {st:<5} {tx}" for n, (_, st, tx) in sorted(bad.items()))
                            + "\nНЕ удаляю инстанс. Разберись на машине (/var/log/wolf.log), "
                              "затем запусти vastgame stop снова.",
                            key="core.upload_unconfirmed", names=sorted(bad))

    # ---- Удаление — только после «да»
    ui.say("Всё выгружено.", "ok", key="core.all_uploaded")
    if not ui.confirm(f"Удалить инстанс {iid}? Диск машины будет стёрт", key="core.ask_delete", id=iid):
        raise VastgameError("Не удаляю. Инстанс продолжает работать и тратить деньги.", key="core.kept")
    ok, msg = delete_instance(iid)
    if not ok:
        raise VastgameError(f"Vast не удалил инстанс: {msg}", key="core.delete_failed", msg=msg or "")
    time.sleep(5)
    gone = iid not in [i["id"] for i in list_instances()]
    if not gone:
        ui.say(f"Vast принял удаление, но {iid} ещё в списке — проверь через минуту.", "warn",
               key="core.delete_pending", id=iid)
    ui.stage("deleted", f"Инстанс {iid} удалён." if gone
             else f"Vast принял удаление, но {iid} ещё в списке — проверь через минуту.")
    return {"iid": iid, "uploaded": uploaded, "deleted": gone}


def emergency_delete(expected_id):
    """Удалить машину БЕЗ выгрузки — для зависшей или сломанной. Удаляет только машину с номером
    expected_id (её человек видел в окне и дважды подтвердил); если сейчас другая — VastgameError.
    Возвращает True, если машина исчезла из списка, False — если Vast ещё не убрал её."""
    inst = list_instances()
    if not inst:
        raise VastgameError("Инстансов нет — удалять нечего.", key="core.no_instances")
    ids = [i["id"] for i in inst]
    if expected_id not in ids:
        raise VastgameError(f"Машины {expected_id} уже нет (сейчас: {', '.join(map(str, ids))}) — ничего не удаляю.",
                            key="core.emergency_mismatch", id=expected_id)
    ok, msg = delete_instance(expected_id)
    if not ok:
        raise VastgameError(f"Vast не удалил инстанс: {msg}", key="core.delete_failed", msg=msg or "")
    for _ in range(6):
        time.sleep(5)
        if expected_id not in [i["id"] for i in list_instances()]:
            return True
    return False

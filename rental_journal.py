# rental_journal.py — устойчивый журнал операций аренды (crash recovery & reconciliation).
#
# Записывает каждый критический переход аренды атомарно на диск (rental_journal.json в config_dir),
# чтобы потеря ответа Vast.ai API, SIGTERM во время PUT asks/, обрыв сети или перезапуск приложения
# никогда не приводили к потере контроля над созданным платным инстансом.

import json
import os
import threading
import time
from pathlib import Path

from settings import config_dir

FILE = config_dir() / "rental_journal.json"
_LOCK = threading.Lock()

# Логическая модель состояний аренды
PREPARED = "prepared"
CREATING = "creating"
CREATED = "created"
CANCEL_REQUESTED = "cancel_requested"
RECONCILING = "reconciling"
DELETING = "deleting"
DELETED = "deleted"
UNCERTAIN = "uncertain"
COMPLETED = "completed"

TERMINAL = frozenset((DELETED, COMPLETED))
ACTIVE_UNRESOLVED = frozenset((CREATING, CANCEL_REQUESTED, RECONCILING, DELETING, UNCERTAIN))
MAX_TERMINAL_HISTORY = 20

# Повторные проверки для учёта задержки появления новой аренды в list_instances() Vast.ai
RECONCILE_POLLS = 4
RECONCILE_STEP = 3.0


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=1)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _load_unlocked():
    if not FILE.exists():
        return {"version": 1, "entries": {}}, False
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
            return raw, False
    except (OSError, ValueError):
        pass
    # Повреждённый журнал не приводит к падению или удалению неподтверждённых инстансов:
    # сохраняем резервную копию .corrupt и помечаем флаг corrupted.
    try:
        backup = FILE.with_name(f"{FILE.name}.corrupt.{int(time.time())}")
        if not backup.exists():
            os.replace(FILE, backup)
    except OSError:
        pass
    return {"version": 1, "entries": {}}, True


def _prune(entries):
    term = sorted(
        ((rid, e.get("updated_at", 0)) for rid, e in entries.items() if e.get("state") in TERMINAL),
        key=lambda x: x[1],
    )
    while len(term) > MAX_TERMINAL_HISTORY:
        rid, _ = term.pop(0)
        entries.pop(rid, None)


def _save_unlocked(doc):
    _prune(doc["entries"])
    _atomic_write(FILE, doc)


def load_all():
    """Вернуть (dict[run_id, entry], corrupted: bool)."""
    with _LOCK:
        doc, corrupted = _load_unlocked()
        return {k: dict(v) for k, v in doc["entries"].items()}, corrupted


def get(run_id):
    if not run_id:
        return None
    with _LOCK:
        doc, _ = _load_unlocked()
        e = doc["entries"].get(str(run_id))
        return dict(e) if e else None


def record(run_id, state, *, iid=None, offer_id=None, cancel_requested=None,
           reason=None, matched_iids=None, note=None):
    """Атомарно создать или обновить запись журнала аренды для run_id."""
    if not run_id:
        return None
    run_id = str(run_id)
    now = time.time()
    with _LOCK:
        doc, _ = _load_unlocked()
        cur = dict(doc["entries"].get(run_id) or {
            "run_id": run_id,
            "label": f"vastgame:{run_id}",
            "state": PREPARED,
            "iid": None,
            "offer_id": None,
            "cancel_requested": False,
            "reason": None,
            "matched_iids": [],
            "created_at": now,
            "updated_at": now,
        })
        # Не позволяем устаревшему воркеру откатить уже подтверждённую отмену/удаление
        if cur.get("cancel_requested") and state in (PREPARED, CREATING, CREATED, COMPLETED):
            if iid is not None and cur.get("iid") is None:
                cur["iid"] = int(iid)
                cur["updated_at"] = now
                doc["entries"][run_id] = cur
                _save_unlocked(doc)
            return dict(cur)
        if cur.get("state") == DELETED and state != DELETED:
            return dict(cur)
        cur["state"] = state
        if iid is not None:
            cur["iid"] = int(iid)
        if offer_id is not None:
            cur["offer_id"] = int(offer_id)
        if cancel_requested is not None:
            cur["cancel_requested"] = bool(cancel_requested)
        if reason is not None:
            cur["reason"] = reason
        if matched_iids is not None:
            cur["matched_iids"] = [int(x) for x in matched_iids]
        if note is not None:
            cur["note"] = str(note)[:240]
        cur["updated_at"] = now
        doc["entries"][run_id] = cur
        _save_unlocked(doc)
        return dict(cur)


def mark_prepared(run_id, offer_id=None):
    return record(run_id, PREPARED, offer_id=offer_id)


def mark_creating(run_id, offer_id=None):
    """Вызывается СТРОГО ДО отправки PUT asks/<offer_id>/ в Vast.ai."""
    return record(run_id, CREATING, offer_id=offer_id)


def mark_created(run_id, iid):
    return record(run_id, CREATED, iid=iid)


def mark_cancel_requested(run_id, iid=None):
    """Фиксирует подтверждённое пользователем намерение отмены/аварийного удаления ДО SIGTERM и DELETE."""
    return record(run_id, CANCEL_REQUESTED, iid=iid, cancel_requested=True)


def mark_completed(run_id, iid=None):
    return record(run_id, COMPLETED, iid=iid)


def mark_deleted(run_id, iid=None, reason=None):
    return record(run_id, DELETED, iid=iid, reason=reason)


def mark_uncertain(run_id, *, iid=None, reason=None, matched_iids=None, note=None):
    return record(run_id, UNCERTAIN, iid=iid, reason=reason, matched_iids=matched_iids, note=note)


def dismiss(run_id):
    """Скрыть визуальное предупреждение после ручной проверки пользователем в консоли Vast.ai.

    НЕ переводит запись в DELETED и НЕ прекращает фоновый технический контроль (reconcile_pending),
    пока отсутствие аренды не будет подтверждено через Vast.ai API.
    """
    if not run_id:
        return False
    run_id = str(run_id)
    with _LOCK:
        doc, _ = _load_unlocked()
        cur = doc["entries"].get(run_id)
        if not cur:
            return False
        cur["dismissed"] = True
        cur["dismissed_at"] = time.time()
        cur["updated_at"] = time.time()
        doc["entries"][run_id] = cur
        _save_unlocked(doc)
        return True


def unresolved(*, include_dismissed=False):
    """Незавершённые/неопределённые записи, требующие внимания или сверки (переживают перезапуск).

    include_dismissed=False — для UI-баннера (не показывает скрытые пользователем, пока нет новой находки).
    include_dismissed=True — для технического контроля reconciliation (включая визуально скрытые).
    """
    entries, corrupted = load_all()
    out = [
        e for e in entries.values()
        if e.get("state") in ACTIVE_UNRESOLVED and (include_dismissed or not e.get("dismissed"))
    ]
    out.sort(key=lambda e: e.get("updated_at", 0), reverse=True)
    if corrupted:
        out.insert(0, {
            "run_id": "_corrupted",
            "label": "",
            "state": UNCERTAIN,
            "iid": None,
            "cancel_requested": False,
            "reason": "journal_corrupted",
            "matched_iids": [],
            "updated_at": time.time(),
        })
    return out


def reconcile_run(run_id, *, core_mod, polls=None, step=None, allow_delete=False):
    """Независимая сверка одной записи журнала с Vast.ai API.

    - Если allow_delete=True (или в записи уже зафиксировано cancel_requested=True):
      удаляет ТОЛЬКО строго подтверждённый инстанс (по известному iid или единственному совпадению vastgame:<run_id>).
    - Никогда не удаляет сторонние инстансы и никогда не удаляет работающую сессию без cancel_requested/allow_delete.
    - При множественных совпадениях, потере сети или отсутствии ответа сохраняет состояние UNCERTAIN.
    Возвращает {"state", "iid", "deleted", "reason", "matched_iids"}.
    """
    from vastapi import VastgameError

    e = get(run_id)
    if not e:
        return {"state": None, "iid": None, "deleted": False, "reason": "not_found", "matched_iids": []}
    if e["state"] == DELETED:
        return {"state": DELETED, "iid": e.get("iid"), "deleted": True, "reason": e.get("reason") or "already_deleted", "matched_iids": []}
    if e["state"] == COMPLETED:
        return {"state": COMPLETED, "iid": e.get("iid"), "deleted": False, "reason": "completed", "matched_iids": []}

    want_delete = bool(allow_delete or e.get("cancel_requested"))
    if e["state"] == PREPARED and want_delete:
        mark_deleted(run_id, reason="cancelled_before_rent")
        return {"state": DELETED, "iid": None, "deleted": True, "reason": "cancelled_before_rent", "matched_iids": []}

    polls = RECONCILE_POLLS if polls is None else max(1, int(polls))
    step = RECONCILE_STEP if step is None else float(step)
    record(run_id, RECONCILING)

    # 1. Если подтверждённый iid уже известен — проверяем/удаляем именно его
    known_iid = e.get("iid")
    if known_iid is not None:
        if not want_delete:
            there = core_mod._still_there(known_iid)
            if there is True:
                mark_created(run_id, known_iid)
                return {"state": CREATED, "iid": known_iid, "deleted": False, "reason": "running_session", "matched_iids": [known_iid]}
            if there is False:
                mark_deleted(run_id, iid=known_iid, reason="already_gone")
                return {"state": DELETED, "iid": known_iid, "deleted": True, "reason": "already_gone", "matched_iids": []}
            mark_uncertain(run_id, iid=known_iid, reason="api_unreachable")
            return {"state": UNCERTAIN, "iid": known_iid, "deleted": False, "reason": "api_unreachable", "matched_iids": []}

        # Подтверждённая отмена с известным iid
        there = core_mod._still_there(known_iid)
        if there is False:
            mark_deleted(run_id, iid=known_iid, reason="already_gone")
            return {"state": DELETED, "iid": known_iid, "deleted": True, "reason": "already_gone", "matched_iids": []}
        record(run_id, DELETING, iid=known_iid)
        try:
            gone = core_mod.emergency_delete(known_iid)
        except VastgameError as err:
            if err.key in ("core.no_instances", "core.emergency_mismatch"):
                mark_deleted(run_id, iid=known_iid, reason="already_gone")
                return {"state": DELETED, "iid": known_iid, "deleted": True, "reason": "already_gone", "matched_iids": []}
            mark_uncertain(run_id, iid=known_iid, reason=err.key or "delete_error", note=str(err))
            return {"state": UNCERTAIN, "iid": known_iid, "deleted": False, "reason": err.key or "delete_error", "matched_iids": []}
        if gone:
            mark_deleted(run_id, iid=known_iid, reason="confirmed_deleted")
            return {"state": DELETED, "iid": known_iid, "deleted": True, "reason": "confirmed_deleted", "matched_iids": []}
        mark_uncertain(run_id, iid=known_iid, reason="delete_pending")
        return {"state": UNCERTAIN, "iid": known_iid, "deleted": False, "reason": "delete_pending", "matched_iids": [known_iid]}

    # 2. iid неизвестен (ответ PUT asks/ потерян или воркер завершён до возврата):
    #    ограниченный опрос list_instances() по точной метке vastgame:<run_id>
    had_api_error = False
    last_err = None
    for attempt in range(polls):
        try:
            matched, _all_ids = core_mod.find_instances_by_run_id(run_id)
        except VastgameError as err:
            had_api_error = True
            last_err = str(err)
            if attempt + 1 < polls and step > 0:
                time.sleep(step)
            continue

        if len(matched) == 1:
            found_iid = matched[0]
            if not want_delete:
                # Восстанавливаем ID живой пользовательской сессии, НЕ удаляя её без разрешения
                mark_created(run_id, found_iid)
                return {"state": CREATED, "iid": found_iid, "deleted": False, "reason": "recovered_iid", "matched_iids": [found_iid]}
            record(run_id, DELETING, iid=found_iid)
            try:
                gone = core_mod.emergency_delete(found_iid)
            except VastgameError as err:
                if err.key in ("core.no_instances", "core.emergency_mismatch"):
                    mark_deleted(run_id, iid=found_iid, reason="already_gone")
                    return {"state": DELETED, "iid": found_iid, "deleted": True, "reason": "already_gone", "matched_iids": []}
                mark_uncertain(run_id, iid=found_iid, reason=err.key or "delete_error", note=str(err))
                return {"state": UNCERTAIN, "iid": found_iid, "deleted": False, "reason": err.key or "delete_error", "matched_iids": [found_iid]}
            if gone:
                mark_deleted(run_id, iid=found_iid, reason="reconciled_and_deleted")
                return {"state": DELETED, "iid": found_iid, "deleted": True, "reason": "reconciled_and_deleted", "matched_iids": []}
            mark_uncertain(run_id, iid=found_iid, reason="delete_pending")
            return {"state": UNCERTAIN, "iid": found_iid, "deleted": False, "reason": "delete_pending", "matched_iids": [found_iid]}

        if len(matched) > 1:
            mark_uncertain(run_id, reason="multiple_matches", matched_iids=matched,
                           note=f"Найдено несколько инстансов с меткой vastgame:{run_id}: {matched}")
            return {"state": UNCERTAIN, "iid": None, "deleted": False, "reason": "multiple_matches", "matched_iids": matched}

        if attempt + 1 < polls and step > 0:
            time.sleep(step)

    # Инстанс не найден по метке после всех попыток: из-за возможной задержки Vast.ai API
    # при прерванном PUT asks/ мы НЕ утверждаем, что аренда гарантированно не создана.
    reason = "api_unreachable" if had_api_error else "unconfirmed_after_creating"
    mark_uncertain(run_id, reason=reason, note=last_err)
    return {"state": UNCERTAIN, "iid": None, "deleted": False, "reason": reason, "matched_iids": []}


def reconcile_pending(*, core_mod, active_run_id=None, polls=1, step=0.0):
    """Сверить все незавершённые записи (например, при запуске приложения или нажатии «Проверить снова»).

    active_run_id — текущий живой запуск play, который ещё идёт (его не трогаем).
    """
    entries, _ = load_all()
    results = {}
    for run_id, e in list(entries.items()):
        if run_id == active_run_id and not e.get("cancel_requested"):
            continue
        if e.get("state") not in ACTIVE_UNRESOLVED:
            continue
        results[run_id] = reconcile_run(
            run_id,
            core_mod=core_mod,
            polls=polls,
            step=step,
            allow_delete=bool(e.get("cancel_requested")),
        )
    return results

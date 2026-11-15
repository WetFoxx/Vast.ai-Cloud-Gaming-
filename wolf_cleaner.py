"""wolf_cleaner.py — стендовый исполнитель безопасной очистки облачных архивов VastGame.

Работает ИСКЛЮЧИТЕЛЬНО на изолированном локальном стендовом каталоге.
Удаляет только доказанных кандидатов (CANDIDATE) при соблюдении всех условий:
1. Под общей блокировкой архива (flock на {base_name}.lock);
2. При наличии активного сессионного допуска (cleanup_grant.json);
3. При свежей классификации без неоднозначностей и повреждений;
4. При неизменности снимка метаданных активных/защищённых данных и квитанций;
5. Со строгой проверкой границ стендового каталога (запрет directory traversal и симлинков);
6. С пошаговым протоколированием оснований удаления и фиксацией остатка при прерывании.
"""

import fcntl
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wolf_classifier import (
    _check_session_authorization,
    _get_remote_listing,
    classify_archive,
    delta_pattern,
    part_re,
)


def is_cleanup_enabled(
    enabled_arg: bool | None = None,
    grant_data: dict | None = None,
    lock_dir: str | Path = "/run/wolf",
) -> bool:
    """Проверка явного включения очистки (по умолчанию ВСЕГДА False)."""
    if enabled_arg is True:
        return True
    if enabled_arg is False:
        return False
    env_val = os.environ.get("VG_STORAGE_CLEANUP_ENABLED", "").lower().strip()
    if env_val in ("1", "true", "yes"):
        return True
    trigger_file = Path(lock_dir) / "cleanup_enabled"
    if trigger_file.is_file():
        try:
            val = trigger_file.read_text(encoding="utf-8").strip().lower()
            if val in ("1", "true", "yes", ""):
                return True
        except OSError:
            pass
    if isinstance(grant_data, dict) and grant_data.get("enabled") is True:
        return True
    return False


class DriveProductionAdapter:
    """Рабочий адаптер для Google Drive с проверкой целевой папки и удалением по file ID."""

    def __init__(self, folder_id: str, access_token: str | None = None, rclone_cmd: str = "rclone"):
        self.folder_id = str(folder_id).strip()
        self.access_token = access_token
        self.rclone_cmd = rclone_cmd

    def _get_token(self) -> str | None:
        if self.access_token:
            return self.access_token
        tok = os.environ.get("GOOGLE_ACCESS_TOKEN")
        if tok:
            self.access_token = tok
            return tok
        rc_conf = os.environ.get("RCLONE_CONFIG", "/root/.config/rclone/rclone.conf")
        if os.path.isfile(rc_conf):
            try:
                with open(rc_conf, "r", encoding="utf-8") as f:
                    content = f.read()
                cid_m = re.search(r"client_id\s*=\s*(.+)", content)
                sec_m = re.search(r"client_secret\s*=\s*(.+)", content)
                tok_m = re.search(r"token\s*=\s*(\{.*?\})", content)
                if tok_m:
                    tok_data = json.loads(tok_m.group(1))
                    ref_tok = tok_data.get("refresh_token")
                    cid = cid_m.group(1).strip() if cid_m else ""
                    csec = sec_m.group(1).strip() if sec_m else ""
                    if ref_tok and cid:
                        req_data = urllib.parse.urlencode({
                            "client_id": cid,
                            "client_secret": csec,
                            "refresh_token": ref_tok,
                            "grant_type": "refresh_token",
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            "https://oauth2.googleapis.com/token",
                            data=req_data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            res = json.loads(resp.read().decode("utf-8"))
                            self.access_token = res.get("access_token")
                            return self.access_token
            except Exception:
                pass
        return None

    def delete_candidate(self, fname: str, cand_info: dict) -> tuple[bool, str | None]:
        cand_fid = cand_info.get("id") or cand_info.get("ID")
        if not cand_fid or not str(cand_fid).strip():
            return False, f"refusing delete: candidate {fname} has no registered file ID (name fallback forbidden)"
        tok = self._get_token()
        if not tok:
            return False, "missing_access_token_for_drive_api"
        try:
            # 1. Проверка целевой папки и имени файла в Drive
            check_url = f"https://www.googleapis.com/drive/v3/files/{cand_fid}?fields=id,name,parents,trashed"
            req = urllib.request.Request(check_url, headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
            if meta.get("name") != fname:
                return False, f"file_name_mismatch: expected {fname}, got {meta.get('name')}"
            parents = meta.get("parents") or []
            if self.folder_id and self.folder_id not in parents:
                return False, f"folder_id_mismatch: expected parent {self.folder_id}, got {parents}"
            if meta.get("trashed"):
                return True, None

            # 2. Удаление строго по ID
            del_req = urllib.request.Request(
                f"https://www.googleapis.com/drive/v3/files/{cand_fid}",
                headers={"Authorization": f"Bearer {tok}"},
                method="DELETE",
            )
            with urllib.request.urlopen(del_req, timeout=20) as resp:
                if resp.status in (200, 204):
                    return True, None
                return False, f"unexpected delete status: {resp.status}"
        except urllib.error.HTTPError as e:
            if e.code in (404,):
                return True, None
            return False, f"Drive API HTTPError: {e.code} ({e.reason})"
        except Exception as e:
            return False, f"Drive API delete exception: {e}"

    def verify_deleted(self, fname: str, cand_info: dict) -> bool:
        cand_fid = cand_info.get("id") or cand_info.get("ID")
        if not cand_fid:
            return False
        tok = self._get_token()
        if not tok:
            return False
        try:
            check_url = f"https://www.googleapis.com/drive/v3/files/{cand_fid}?fields=id,trashed"
            req = urllib.request.Request(check_url, headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
                return bool(meta.get("trashed"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True
            return False
        except Exception:
            return False


def capture_storage_state(
    stand_root: Path | str,
    tmp_dir: str | Path = "",
    state_dir: str | Path = "",
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> dict:
    """Фиксирует базовое состояние хранилища и квитанций ДО классификации."""
    state = {
        "files": {},
        "receipts": {},
    }
    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing:
            for item in listing:
                state["files"][item["Name"]] = (item["Size"], item.get("ModTime_epoch", 0.0), item.get("MD5", ""))
    else:
        p_root = Path(stand_root)
        if p_root.is_dir():
            try:
                for f in p_root.iterdir():
                    if f.is_file():
                        st = f.stat()
                        state["files"][f.name] = (st.st_size, st.st_mtime)
            except OSError:
                pass

    if tmp_dir and os.path.isdir(tmp_dir):
        try:
            for entry in os.listdir(tmp_dir):
                if entry.startswith("attempt-"):
                    p = Path(tmp_dir) / entry
                    if p.is_file():
                        st = p.stat()
                        state["receipts"][str(p)] = (st.st_size, st.st_mtime)
        except OSError:
            pass

    if state_dir and os.path.isdir(state_dir):
        p_dir = Path(state_dir) / "pending"
        if p_dir.is_dir():
            try:
                for entry in os.listdir(p_dir):
                    p = p_dir / entry
                    if p.is_file():
                        st = p.stat()
                        state["receipts"][str(p)] = (st.st_size, st.st_mtime)
            except OSError:
                pass

    return state


def verify_storage_state(
    stand_root: Path | str,
    expected_state: dict,
    tmp_dir: str | Path = "",
    state_dir: str | Path = "",
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> tuple[bool, str | None]:
    """Проверяет неизменность состояния хранилища и квитанций относительно expected_state."""
    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    current_files = {}
    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing is None:
            return False, "storage directory listing failed"
        for item in listing:
            current_files[item["Name"]] = (item["Size"], item.get("ModTime_epoch", 0.0), item.get("MD5", ""))
    else:
        p_root = Path(stand_root)
        if not p_root.is_dir():
            return False, "stand root directory disappeared"
        try:
            for f in p_root.iterdir():
                if f.is_file():
                    st = f.stat()
                    current_files[f.name] = (st.st_size, st.st_mtime)
        except OSError as e:
            return False, f"storage directory stat error: {e}"

    if set(current_files.keys()) != set(expected_state.get("files", {}).keys()):
        return False, "storage directory file list changed"

    for fname, exp_val in expected_state.get("files", {}).items():
        if fname not in current_files:
            return False, f"file disappeared: {fname}"
        exp_sz = exp_val[0]
        cur_sz = current_files[fname][0]
        if cur_sz != exp_sz:
            return False, f"file size changed: {fname}"
        if not is_remote:
            exp_mt = exp_val[1]
            cur_mt = current_files[fname][1]
            if abs(cur_mt - exp_mt) > 1e-4:
                return False, f"file mtime changed: {fname}"
        else:
            if len(exp_val) > 2 and exp_val[2] and current_files[fname][2]:
                if current_files[fname][2] != exp_val[2]:
                    return False, f"file hash changed: {fname}"

    for rpath_str, (exp_sz, exp_mt) in expected_state.get("receipts", {}).items():
        rp = Path(rpath_str)
        if not rp.is_file():
            return False, f"receipt file disappeared: {rpath_str}"
        try:
            st = rp.stat()
            if st.st_size != exp_sz or abs(st.st_mtime - exp_mt) > 1e-4:
                return False, f"receipt file modified: {rpath_str}"
        except OSError as e:
            return False, f"receipt file error: {rpath_str} ({e})"

    return True, None


def compute_storage_snapshot(
    stand_root: Path | str,
    report: dict,
    tmp_dir: str | Path,
    state_dir: str | Path,
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> dict:
    """Фиксирует снимок метаданных, на котором основано решение об очистке:
    - состояние активной базы и дельты;
    - размеры и отпечатки активных и защищённых файлов;
    - состав кандидатов;
    - состояние файлов квитанций (attempt-* и pending).
    """
    snapshot = {
        "active_base_kind": report.get("active_base_kind"),
        "active_base_version": report.get("active_base_version"),
        "active_delta_kind": report.get("active_delta_kind"),
        "active_delta_version": report.get("active_delta_version"),
        "active_protected_files": {},
        "candidate_files": {},
        "all_storage_files": {},
        "receipt_files": {},
    }

    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    remote_items_by_name = {}
    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing:
            snapshot["all_storage_files"] = {item["Name"]: item["Size"] for item in listing}
            remote_items_by_name = {item["Name"]: item for item in listing}
    else:
        p_root = Path(stand_root)
        try:
            snapshot["all_storage_files"] = {f.name: f.stat().st_size for f in p_root.iterdir() if f.is_file()}
        except OSError:
            pass

    # Снимок активных и защищённых файлов в хранилище
    for f in report.get("files", []):
        cat = f.get("category")
        fname = f.get("name")
        if is_remote:
            it = remote_items_by_name.get(fname)
            val = {
                "size": it["Size"],
                "mtime": it.get("ModTime_epoch", 0.0),
                "md5": it.get("MD5", ""),
            } if it else None
            if cat in ("ACTIVE", "PROTECTED"):
                snapshot["active_protected_files"][fname] = val
            elif cat == "CANDIDATE":
                snapshot["candidate_files"][fname] = val
        else:
            p_root = Path(stand_root)
            fpath = p_root / fname
            if cat in ("ACTIVE", "PROTECTED"):
                if fpath.is_file():
                    try:
                        st = fpath.stat()
                        snapshot["active_protected_files"][fname] = {
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "md5": f.get("md5"),
                        }
                    except OSError:
                        snapshot["active_protected_files"][fname] = None
            elif cat == "CANDIDATE":
                if fpath.is_file():
                    try:
                        st = fpath.stat()
                        snapshot["candidate_files"][fname] = {
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "md5": f.get("md5"),
                        }
                    except OSError:
                        snapshot["candidate_files"][fname] = None

    # Снимок файлов квитанций (attempt-*)
    base_tar = report.get("object", "")
    if tmp_dir and os.path.isdir(tmp_dir):
        base_att_prefix = "attempt-" + base_tar.replace("/", "_")
        try:
            for entry in os.listdir(tmp_dir):
                if entry == base_att_prefix or entry.startswith(base_att_prefix + "."):
                    p = Path(tmp_dir) / entry
                    if p.is_file():
                        st = p.stat()
                        snapshot["receipt_files"][str(p)] = (st.st_size, st.st_mtime)
        except OSError:
            pass

    # Снимок постоянных квитанций (pending)
    base_name = report.get("base_name", "")
    if state_dir and os.path.isdir(state_dir):
        p_dir = Path(state_dir) / "pending"
        if p_dir.is_dir():
            for cname in (base_name, base_tar):
                p = p_dir / cname
                if p.is_file():
                    st = p.stat()
                    snapshot["receipt_files"][str(p)] = (st.st_size, st.st_mtime)

    return snapshot


def verify_storage_snapshot(
    stand_root: Path | str,
    snapshot: dict,
    tmp_dir: str | Path,
    state_dir: str | Path,
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> tuple[bool, str | None]:
    """Проверяет, не изменилось ли состояние активных данных, защиты и квитанций перед удалением."""
    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    remote_items = {}
    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing is None:
            return False, "storage listing failed"
        current_files = {item["Name"]: item["Size"] for item in listing}
        remote_items = {item["Name"]: item for item in listing}
    else:
        p_root = Path(stand_root)
        try:
            current_files = {f.name: f.stat().st_size for f in p_root.iterdir() if f.is_file()}
        except OSError as e:
            return False, f"storage directory stat error: {e}"

    # 0. Проверка неизменности состава файлов в стенде
    if snapshot.get("all_storage_files"):
        if set(current_files.keys()) != set(snapshot["all_storage_files"].keys()):
            return False, "storage directory file list changed between classification and execution"
        for fn, exp_sz in snapshot["all_storage_files"].items():
            if current_files.get(fn) != exp_sz:
                return False, f"file size changed between classification and execution: {fn}"

    # 1. Проверка активных и защищённых файлов
    for fname, expected in snapshot.get("active_protected_files", {}).items():
        if is_remote:
            it = remote_items.get(fname)
            if expected is None:
                if it is not None:
                    return False, f"active/protected file appeared unexpectedly: {fname}"
                continue
            if it is None:
                return False, f"active/protected file missing: {fname}"
            if it["Size"] != expected["size"]:
                return False, f"active/protected file size changed: {fname}"
            if expected.get("md5") and it.get("MD5") and it["MD5"] != expected["md5"]:
                return False, f"active/protected file hash changed: {fname}"
        else:
            p_root = Path(stand_root)
            fpath = p_root / fname
            if expected is None:
                if fpath.is_file():
                    return False, f"active/protected file appeared unexpectedly: {fname}"
                continue
            if not fpath.is_file():
                return False, f"active/protected file missing: {fname}"
            try:
                st = fpath.stat()
                if st.st_size != expected["size"]:
                    return False, f"active/protected file size changed: {fname}"
                if abs(st.st_mtime - expected["mtime"]) > 1e-4:
                    return False, f"active/protected file mtime changed: {fname}"
            except OSError as e:
                return False, f"active/protected file stat error: {fname} ({e})"

    # 2. Проверка файлов-кандидатов
    for fname, expected in snapshot.get("candidate_files", {}).items():
        if is_remote:
            it = remote_items.get(fname)
            if expected is None:
                continue
            if it is None:
                return False, f"candidate file missing before deletion: {fname}"
            if it["Size"] != expected["size"]:
                return False, f"candidate file size changed: {fname}"
            if expected.get("md5") and it.get("MD5") and it["MD5"] != expected["md5"]:
                return False, f"candidate file hash changed: {fname}"
        else:
            p_root = Path(stand_root)
            fpath = p_root / fname
            if expected is None:
                continue
            if not fpath.is_file():
                return False, f"candidate file missing before deletion: {fname}"
            try:
                st = fpath.stat()
                if st.st_size != expected["size"]:
                    return False, f"candidate file size changed: {fname}"
                if abs(st.st_mtime - expected["mtime"]) > 1e-4:
                    return False, f"candidate file mtime changed: {fname}"
            except OSError as e:
                return False, f"candidate file stat error: {fname} ({e})"

    # 3. Проверка файлов квитанций
    for rpath_str, expected in snapshot.get("receipt_files", {}).items():
        rp = Path(rpath_str)
        if not rp.is_file():
            return False, f"receipt file disappeared: {rpath_str}"
        try:
            st = rp.stat()
            if (st.st_size, st.st_mtime) != expected:
                return False, f"receipt file modified: {rpath_str}"
        except OSError as e:
            return False, f"receipt file error: {rpath_str} ({e})"

    return True, None


def load_or_init_run_report(
    report_path: Path | str,
    run_id: str | None,
    iid: str | None,
    folder_id: str | None,
    all_objects: list[str] | None = None,
    clean_pass_id: str | None = None,
) -> dict:
    """Загружает существующий отчёт текущего прогона или инициализирует новый."""
    p = Path(report_path)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            matches_run = False
            if run_id and data.get("run_id") == run_id:
                matches_run = True
            elif run_id is None and data.get("run_id"):
                matches_run = True
                run_id = data.get("run_id")
            elif run_id is None and not data.get("run_id"):
                matches_run = True

            # Если задан идентификатор отдельного прохода очистки, он обязан совпадать
            if clean_pass_id and data.get("clean_pass_id") != clean_pass_id:
                matches_run = False

            if matches_run and "archives" in data and isinstance(data["archives"], dict):
                if not data.get("clean_pass_id") and clean_pass_id:
                    data["clean_pass_id"] = clean_pass_id
                if not data.get("iid") and iid:
                    data["iid"] = iid
                if not data.get("folder_id") and folder_id:
                    data["folder_id"] = folder_id
                if all_objects:
                    for obj in all_objects:
                        bname = obj[:-8] if obj.endswith(".tar.zst") else obj
                        if bname not in data["archives"]:
                            data["archives"][bname] = {
                                "object": bname,
                                "status": "pending",
                                "reason": None,
                                "candidates": [],
                                "deleted": [],
                                "unconfirmed": [],
                                "skipped": [],
                                "deleted_count": 0,
                                "unconfirmed_count": 0,
                                "skipped_count": 0,
                                "deleted_bytes": 0,
                            }
                sync_run_report_totals(data)
                return data
        except Exception:
            pass

    data = {
        "run_id": run_id,
        "session_id": run_id,
        "clean_pass_id": clean_pass_id,
        "iid": iid,
        "folder_id": folder_id,
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "in_progress",
        "reason": None,
        "total_candidates": 0,
        "deleted_count": 0,
        "unconfirmed_count": 0,
        "skipped_count": 0,
        "deleted_bytes": 0,
        "archives": {},
        "candidates": [],
        "deleted": [],
        "unconfirmed": [],
        "skipped": [],
    }
    if all_objects:
        for obj in all_objects:
            bname = obj[:-8] if obj.endswith(".tar.zst") else obj
            data["archives"][bname] = {
                "object": bname,
                "status": "pending",
                "reason": None,
                "candidates": [],
                "deleted": [],
                "unconfirmed": [],
                "skipped": [],
                "deleted_count": 0,
                "unconfirmed_count": 0,
                "skipped_count": 0,
                "deleted_bytes": 0,
            }
    return data


def sync_run_report_totals(report: dict) -> None:
    """Агрегирует счётчики и определяет статус всего прогона очистки."""
    all_candidates = []
    all_deleted = []
    all_unconfirmed = []
    all_skipped = []
    total_deleted_bytes = 0

    has_error = False
    has_stopped = False
    has_in_progress = False
    all_clean = True

    for arch_name, arch_data in report.get("archives", {}).items():
        st = arch_data.get("status")
        if st in ("failed", "aborted", "forbidden", "rejected"):
            has_error = True
            all_clean = False
        elif st == "stopped":
            has_stopped = True
            all_clean = False
        elif st in ("in_progress", "pending"):
            has_in_progress = True
            all_clean = False
        elif st not in ("completed", "noop", "disabled"):
            all_clean = False

        cands = arch_data.get("candidates", [])
        all_candidates.extend(cands)
        total_deleted_bytes += arch_data.get("deleted_bytes", 0)

        for c in cands:
            c_state = c.get("state")
            if c_state == "CONFIRMED_DELETED":
                all_deleted.append(c)
            elif c_state in ("UNCONFIRMED_UNKNOWN", "UNCONFIRMED_ERROR"):
                all_unconfirmed.append(c)
            elif c_state == "SKIPPED_UNEXECUTED":
                all_skipped.append(c)

        arch_data["deleted"] = [c for c in cands if c.get("state") == "CONFIRMED_DELETED"]
        arch_data["unconfirmed"] = [c for c in cands if c.get("state") in ("UNCONFIRMED_UNKNOWN", "UNCONFIRMED_ERROR")]
        arch_data["skipped"] = [c for c in cands if c.get("state") == "SKIPPED_UNEXECUTED"]
        arch_data["deleted_count"] = len(arch_data["deleted"])
        arch_data["unconfirmed_count"] = len(arch_data["unconfirmed"])
        arch_data["skipped_count"] = len(arch_data["skipped"])

    report["candidates"] = all_candidates
    report["deleted"] = all_deleted
    report["unconfirmed"] = all_unconfirmed
    report["skipped"] = all_skipped
    report["total_candidates"] = len(all_candidates)
    report["deleted_count"] = len(all_deleted)
    report["unconfirmed_count"] = len(all_unconfirmed)
    report["skipped_count"] = len(all_skipped)
    report["deleted_bytes"] = total_deleted_bytes
    report["updated_at"] = time.time()

    if has_stopped:
        report["status"] = "stopped"
    elif has_error:
        report["status"] = "warn"
    elif has_in_progress:
        report["status"] = "in_progress"
    elif all_clean:
        report["status"] = "completed"


def flush_atomic_report(report_path: Path | str, report: dict) -> None:
    """Атомарно сбрасывает JSON отчёта через временный файл."""
    p = Path(report_path)
    tmp_p = p.with_name(f"{p.name}.tmp.{os.getpid()}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_p, p)
    except Exception:
        pass


def execute_stand_cleanup(
    stand_dir: str | Path,
    obj_name: str | list[str],
    grant_file: str | Path = "/run/wolf/cleanup_grant.json",
    lock_dir: str | Path = "/run/wolf",
    tmp_dir: str | Path = "/var/tmp/wolf",
    state_dir: str | Path = "/var/lib/wolf",
    run_id_val: str | None = None,
    iid_val: str | None = None,
    folder_id_val: str | None = None,
    dry_run: bool = False,
    log_fn=None,
    allow_interruption_hook: bool = True,
    drive_test_mode: bool = False,
    drive_test_folder_id: str | None = None,
    drive_test_registry: dict | None = None,
    rclone_cmd: str = "rclone",
    drive_adapter=None,
    drive_production_mode: bool = False,
    enabled: bool | None = None,
    report_file: str | Path | None = None,
    access_token: str | None = None,
    require_isolation: bool = False,
    clean_pass_id: str | None = None,
) -> dict:
    """Исполняет безопасную очистку одного или нескольких архивов.

    Возвращает словарь с точным результатом выполнения:
    - status: 'completed' | 'stopped' | 'aborted' | 'forbidden' | 'noop' | 'rejected' | 'disabled' | 'warn'
    - reason: текстовое пояснение статуса
    - deleted: список словарей удалённых объектов [{'name': ..., 'reason': ..., 'size': ...}]
    - deleted_bytes: суммарный локальный размер удалённых файлов
    - remaining: список кандидатов, оставшихся неудалёнными (при остановке/ошибке)
    - report: свежий отчёт классификатора (для одиночного архива)
    - log_messages: список журнальных сообщений
    - candidate_states: детальный статус каждого кандидата
    - archives: словарь с деталями по каждому архиву
    """
    log_messages = []

    def _log(msg: str):
        log_messages.append(msg)
        if log_fn:
            log_fn(msg)

    if isinstance(obj_name, (list, tuple)):
        target_objects = [str(o).strip() for o in obj_name if str(o).strip()]
    else:
        target_objects = [str(obj_name).strip()] if str(obj_name).strip() else []

    if not target_objects:
        return {
            "status": "noop",
            "reason": "no_objects_specified",
            "deleted": [],
            "deleted_bytes": 0,
            "remaining": [],
            "report": {},
            "log_messages": ["нет объектов для очистки"],
            "archives": {},
            "total_candidates": 0,
            "deleted_count": 0,
            "unconfirmed_count": 0,
            "skipped_count": 0,
        }

    # 0. Проверка выключателя: по умолчанию ВСЕГДА выключено
    grant_data = None
    if os.path.isfile(str(grant_file)):
        try:
            with open(str(grant_file), "r", encoding="utf-8") as gf:
                grant_data = json.load(gf)
        except Exception:
            pass


    stand_str = str(stand_dir).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    # Независимое определение метаданных текущей сессии строго по доверенному состоянию сервера.
    # Подстановка из grant_data строго запрещена: допуск должен сверяться против доверенных данных.
    if run_id_val is None:
        p_rid = Path(lock_dir) / "run_id"
        if p_rid.is_file():
            try:
                run_id_val = p_rid.read_text("utf-8").strip() or None
            except Exception:
                pass
        if run_id_val is None and os.environ.get("WOLF_RUN_ID"):
            run_id_val = os.environ.get("WOLF_RUN_ID").strip() or None
        if run_id_val is None:
            try:
                from wolf_session import get_run_id
                run_id_val = get_run_id(str(p_rid)) or get_run_id()
            except Exception:
                pass

    if iid_val is None:
        if os.environ.get("VI"):
            iid_val = os.environ.get("VI").strip() or None
        elif (Path(lock_dir) / "iid").is_file():
            try:
                iid_val = (Path(lock_dir) / "iid").read_text("utf-8").strip() or None
            except Exception:
                pass
        if iid_val is None:
            try:
                from wolf_session import get_env_iid
                iid_val = get_env_iid(str(Path(lock_dir) / "iid")) or get_env_iid()
            except Exception:
                pass

    if folder_id_val is None:
        p_fid = Path(lock_dir) / "storage_folder_id"
        if p_fid.is_file():
            try:
                folder_id_val = p_fid.read_text("utf-8").strip() or None
            except Exception:
                pass
        elif os.environ.get("WOLF_STORAGE_FOLDER_ID"):
            folder_id_val = os.environ.get("WOLF_STORAGE_FOLDER_ID").strip() or None
        elif drive_test_mode and drive_test_folder_id:
            folder_id_val = drive_test_folder_id
        elif is_remote and (drive_production_mode or drive_adapter is not None):
            try:
                from wolf_session import get_storage_folder_id
                folder_id_val = get_storage_folder_id(r_val=stand_str, rc_cmd=rclone_cmd)
            except Exception:
                pass


    if enabled is False:
        clean_enabled = False
    elif enabled is True:
        clean_enabled = True
    elif drive_production_mode:
        clean_enabled = is_cleanup_enabled(None, grant_data=grant_data, lock_dir=str(lock_dir))
    else:
        clean_enabled = True if enabled is None else is_cleanup_enabled(enabled, grant_data=grant_data, lock_dir=str(lock_dir))

    if clean_pass_id is None:
        if os.environ.get("WOLF_CLEANUP_PASS_ID"):
            clean_pass_id = os.environ.get("WOLF_CLEANUP_PASS_ID").strip() or None
        elif (Path(lock_dir) / "cleanup_pass_id").is_file():
            try:
                clean_pass_id = (Path(lock_dir) / "cleanup_pass_id").read_text("utf-8").strip() or None
            except Exception:
                pass
        if clean_pass_id is None:
            clean_pass_id = f"pass_{int(time.time())}_{os.getpid()}"

    report_path = Path(report_file) if report_file else (Path(lock_dir) / "cleanup_report.json")
    run_report = load_or_init_run_report(report_path, run_id_val, iid_val, folder_id_val, target_objects, clean_pass_id=clean_pass_id)

    if not clean_enabled:
        _log("очистка выключена по умолчанию (требуется явное включение opt-in)")
        for obj in target_objects:
            bname = obj[:-8] if obj.endswith(".tar.zst") else obj
            run_report["archives"][bname]["status"] = "disabled"
            run_report["archives"][bname]["reason"] = "cleanup_disabled_by_default"
        run_report["status"] = "disabled"
        run_report["reason"] = "cleanup_disabled_by_default"
        sync_run_report_totals(run_report)
        flush_atomic_report(report_path, run_report)
        return {
            "status": "disabled",
            "reason": "cleanup_disabled_by_default",
            "deleted": [],
            "deleted_bytes": 0,
            "remaining": [],
            "report": {},
            "log_messages": log_messages,
            "archives": run_report["archives"],
            "total_candidates": 0,
            "deleted_count": 0,
            "unconfirmed_count": 0,
            "skipped_count": 0,
        }

    # 1. Валидация стендового каталога или удалённого режима Drive
    if is_remote:
        if drive_test_mode:
            if not drive_test_folder_id:
                return {
                    "status": "rejected",
                    "reason": "drive_test_folder_id_required",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: не указан folder ID тестовой папки Drive"],
                    "archives": run_report["archives"],
                }
            prod_folder_id = "1vXf2vBiywk0SVf0bFPJTWfcg1Er9pemy"
            if drive_test_folder_id == prod_folder_id or "vastai-cloud-games" in stand_str:
                return {
                    "status": "rejected",
                    "reason": "production_folder_forbidden",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: использование рабочей папки vastai-cloud-games строго запрещено"],
                    "archives": run_report["archives"],
                }
            if drive_test_registry is None:
                return {
                    "status": "rejected",
                    "reason": "drive_test_registry_required",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: реестр тестового запуска обязателен для drive_test_mode"],
                    "archives": run_report["archives"],
                }
            if folder_id_val and folder_id_val != drive_test_folder_id:
                return {
                    "status": "rejected",
                    "reason": "folder_id_mismatch",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": [f"отказ: folder_id сессии ({folder_id_val}) не совпадает со стендовым ({drive_test_folder_id})"],
                    "archives": run_report["archives"],
                }
            stand_root = stand_str
        elif drive_production_mode or drive_adapter is not None:
            effective_fid = folder_id_val
            if not effective_fid:
                try:
                    from wolf_session import get_storage_folder_id
                    effective_fid = get_storage_folder_id(r_val=stand_str, rc_cmd=rclone_cmd)
                except Exception:
                    pass
            if not effective_fid:
                return {
                    "status": "rejected",
                    "reason": "drive_folder_id_required",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: не определена целевая папка Drive для сессии"],
                    "archives": run_report["archives"],
                }
            if drive_adapter is None:
                drive_adapter = DriveProductionAdapter(
                    folder_id=effective_fid,
                    access_token=access_token,
                    rclone_cmd=rclone_cmd,
                )
            stand_root = stand_str
        else:
            return {
                "status": "rejected",
                "reason": "remote_storage_forbidden_local_stand_only",
                "deleted": [],
                "deleted_bytes": 0,
                "remaining": [],
                "log_messages": ["отказ: удаление разрешено только на локальном стендовом каталоге"],
                "archives": run_report["archives"],
            }
    else:
        stand_path = Path(stand_str)
        if not stand_path.is_dir():
            return {
                "status": "rejected",
                "reason": "stand_directory_not_found",
                "deleted": [],
                "deleted_bytes": 0,
                "remaining": [],
                "log_messages": [f"отказ: каталог стенда не найден: {stand_str}"],
                "archives": run_report["archives"],
            }
        stand_root = stand_path.resolve()

    # Установка обработчиков сигналов прерывания
    active_archive = None

    def _sig_handler(signum, frame):
        if active_archive and active_archive in run_report.get("archives", {}):
            arch_entry = run_report["archives"][active_archive]
            arch_entry["status"] = "stopped"
            arch_entry["reason"] = f"received signal {signum}"
        run_report["status"] = "stopped"
        run_report["reason"] = f"received signal {signum}"
        sync_run_report_totals(run_report)
        flush_atomic_report(report_path, run_report)
        sys.exit(128 + signum)

    old_term = None
    old_int = None
    try:
        old_term = signal.signal(signal.SIGTERM, _sig_handler)
        old_int = signal.signal(signal.SIGINT, _sig_handler)
    except (ValueError, AttributeError):
        pass

    # Проверка изоляции группы процессов ДО разрешения удаления объектов
    parent_pgid = os.environ.get("WOLF_PARENT_PGID")
    must_isolate = require_isolation or bool(parent_pgid) or bool(os.environ.get("VG_REQUIRE_ISOLATION"))
    if must_isolate:
        my_pgrp = os.getpgrp()
        iso_ok = True
        iso_err = None
        if my_pgrp <= 1:
            iso_ok = False
            iso_err = f"process group {my_pgrp} is invalid"
        elif parent_pgid and str(my_pgrp) == str(parent_pgid):
            iso_ok = False
            iso_err = f"process group {my_pgrp} is identical to Wolf parent PGID {parent_pgid}"
        else:
            iso_file = Path(lock_dir) / "cleanup_isolated"
            t_iso_start = time.time()
            while time.time() - t_iso_start < 2.0:
                if iso_file.is_file():
                    break
                time.sleep(0.05)
            if not iso_file.is_file():
                iso_ok = False
                iso_err = "cleanup_isolated confirmation handshake file missing"
            else:
                try:
                    conf_pgid = iso_file.read_text("utf-8").strip()
                    if conf_pgid and conf_pgid != str(my_pgrp):
                        iso_ok = False
                        iso_err = f"cleanup_isolated PGID {conf_pgid} != my PGID {my_pgrp}"
                except Exception as e:
                    iso_ok = False
                    iso_err = f"cannot read cleanup_isolated: {e}"

        if not iso_ok:
            _log(f"ошибка безопасности: изоляция группы процессов не подтверждена: {iso_err}")
            for obj in target_objects:
                bname = obj[:-8] if str(obj).endswith(".tar.zst") else str(obj)
                arch_e = run_report["archives"].setdefault(bname, {
                    "object": bname, "status": "aborted", "reason": f"process_isolation_failed: {iso_err}",
                    "candidates": [], "deleted": [], "unconfirmed": [], "skipped": [],
                    "deleted_count": 0, "unconfirmed_count": 0, "skipped_count": 0, "deleted_bytes": 0,
                })
                arch_e["status"] = "aborted"
                arch_e["reason"] = f"process_isolation_failed: {iso_err}"
            run_report["status"] = "aborted"
            run_report["reason"] = f"process_isolation_failed: {iso_err}"
            sync_run_report_totals(run_report)
            flush_atomic_report(report_path, run_report)
            return {
                "status": "aborted",
                "reason": f"process_isolation_failed: {iso_err}",
                "deleted": [],
                "deleted_bytes": 0,
                "remaining": [],
                "report": {},
                "log_messages": log_messages,
                "archives": run_report["archives"],
                "total_candidates": 0,
                "deleted_count": 0,
                "unconfirmed_count": 0,
                "skipped_count": 0,
            }

    stop_all = False
    try:
        for target_obj in target_objects:
            if stop_all:
                break

            obj_str = str(target_obj).strip()
            if obj_str.endswith(".tar.zst"):
                base_tar = obj_str
                base_name = obj_str[:-8]
            else:
                base_name = obj_str
                base_tar = f"{obj_str}.tar.zst"

            active_archive = base_name
            arch_entry = run_report["archives"].setdefault(base_name, {
                "object": base_name,
                "status": "pending",
                "reason": None,
                "candidates": [],
                "deleted": [],
                "unconfirmed": [],
                "skipped": [],
                "deleted_count": 0,
                "unconfirmed_count": 0,
                "skipped_count": 0,
                "deleted_bytes": 0,
            })
            arch_entry["status"] = "in_progress"
            sync_run_report_totals(run_report)
            flush_atomic_report(report_path, run_report)

            # 2. Захват общей блокировки архива ({base_name}.lock)
            lock_file = Path(lock_dir) / f"{base_name}.lock"
            lock_file.parent.mkdir(parents=True, exist_ok=True)

            try:
                lock_fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
            except OSError as e:
                arch_entry["status"] = "aborted"
                arch_entry["reason"] = f"lock_file_open_failed: {e}"
                _log(f"ошибка дескриптора блокировки {lock_file.name}: {e}")
                sync_run_report_totals(run_report)
                flush_atomic_report(report_path, run_report)
                continue

            try:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError):
                    _log(f"блокировка {lock_file.name} занята другой операцией (запись/восстановление/очистка) — отказ")
                    arch_entry["status"] = "aborted"
                    arch_entry["reason"] = "archive_lock_busy"
                    arch_entry["lock_file"] = str(lock_file)
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                _log(f"захвачена блокировка архива: {lock_file.name}")

                # 3. Проверка разрешения текущей сессии под блокировкой
                effective_fid = drive_test_folder_id if (drive_test_mode and folder_id_val is None) else folder_id_val
                has_auth = _check_session_authorization(
                    grant_file=str(grant_file),
                    run_id_val=run_id_val,
                    iid_val=iid_val,
                    folder_id_val=effective_fid,
                )
                if not has_auth:
                    _log("сессионное разрешение отсутствует или устарело — выполнение очистки запрещено (VIEW-ONLY)")
                    arch_entry["status"] = "forbidden"
                    arch_entry["reason"] = "view_only_no_session_grant"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 4. Фиксация базового состояния хранилища ДО классификации
                pre_snapshot = capture_storage_state(stand_root, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)

                # 5. Построение свежей классификации под блокировкой
                report = classify_archive(
                    remote=str(stand_root),
                    obj_name=base_tar,
                    grant_file=str(grant_file),
                    tmp_dir=str(tmp_dir),
                    state_dir=str(state_dir),
                    run_id_val=run_id_val,
                    iid_val=iid_val,
                    folder_id_val=effective_fid,
                    rclone_cmd=rclone_cmd,
                )
                arch_entry["classification_report"] = report

                # Управляемое изменение между классификацией и подтверждением неизменности
                if allow_interruption_hook and hasattr(execute_stand_cleanup, "_post_classify_hook"):
                    try:
                        execute_stand_cleanup._post_classify_hook()
                    except Exception as e:
                        _log(f"post_classify_hook вызвал исключение: {e}")

                # 6. Проверка неизменности состояния ПОСЛЕ классификации
                post_ok, post_err = verify_storage_state(stand_root, pre_snapshot, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)
                if not post_ok:
                    _log(f"отмена плана: обнаружено параллельное изменение состояния хранилища во время классификации ({post_err})")
                    arch_entry["status"] = "aborted"
                    arch_entry["reason"] = f"metadata_snapshot_mismatch: {post_err}"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                if not report.get("cleanup_allowed"):
                    block_reason = report.get("cleanup_block_reason") or "cleanup_not_allowed"
                    if block_reason == "no_candidates_to_clean":
                        _log("доказанных кандидатов на очистку нет (0 файлов)")
                        arch_entry["status"] = "noop"
                        arch_entry["reason"] = "no_candidates_to_clean"
                    else:
                        _log(f"очистка запрещена классификатором: {block_reason}")
                        arch_entry["status"] = "forbidden"
                        arch_entry["reason"] = block_reason
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                candidates = [f for f in report.get("files", []) if f.get("category") == "CANDIDATE"]
                if not candidates:
                    _log("доказанных кандидатов на очистку нет (0 файлов)")
                    arch_entry["status"] = "noop"
                    arch_entry["reason"] = "no_candidates_to_clean"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 7. Проверка грамматики и путей кандидатов
                pr_b = part_re(base_tar)
                d_pat = delta_pattern(base_tar)
                grammar_violated = False
                for cand in candidates:
                    fname = cand["name"]
                    is_valid_grammar = (
                        fname == base_tar
                        or fname == f"{base_tar}.parts"
                        or bool(pr_b.fullmatch(fname))
                        or bool(d_pat.match(fname))
                    )
                    if not is_valid_grammar:
                        _log(f"ошибка безопасности: кандидат {fname} не соответствует грамматике архива")
                        arch_entry["status"] = "rejected"
                        arch_entry["reason"] = f"candidate_grammar_violation: {fname}"
                        grammar_violated = True
                        break

                    if is_remote:
                        if drive_test_mode:
                            if fname not in drive_test_registry:
                                _log(f"ошибка безопасности: кандидат {fname} отсутствует в реестре тестового запуска")
                                arch_entry["status"] = "rejected"
                                arch_entry["reason"] = f"candidate_not_in_test_registry: {fname}"
                                grammar_violated = True
                                break
                            reg_info = drive_test_registry[fname]
                            cand_fid = reg_info.get("id") if isinstance(reg_info, dict) else None
                        else:
                            cand_fid = cand.get("id") or cand.get("ID")

                        if not cand_fid or not str(cand_fid).strip():
                            _log(f"ошибка безопасности: кандидат {fname} не имеет зарегистрированного file ID (удаление по имени запрещено)")
                            arch_entry["status"] = "rejected"
                            arch_entry["reason"] = f"candidate_missing_registered_file_id: {fname}"
                            grammar_violated = True
                            break
                    else:
                        cand_file = stand_root / fname
                        if cand_file.is_symlink():
                            resolved_target = cand_file.resolve()
                            if not resolved_target.is_relative_to(stand_root):
                                _log(f"ошибка безопасности: обнаружен симлинк за пределы стенда: {fname}")
                                arch_entry["status"] = "rejected"
                                arch_entry["reason"] = f"symlink_traversal_detected: {fname}"
                                grammar_violated = True
                                break

                        try:
                            resolved_cand = cand_file.resolve()
                            if not resolved_cand.is_relative_to(stand_root):
                                _log(f"ошибка безопасности: путь выходит за пределы каталога стенда: {fname}")
                                arch_entry["status"] = "rejected"
                                arch_entry["reason"] = f"directory_traversal_detected: {fname}"
                                grammar_violated = True
                                break
                        except OSError as e:
                            arch_entry["status"] = "aborted"
                            arch_entry["reason"] = f"path_resolve_error: {fname} ({e})"
                            grammar_violated = True
                            break

                if grammar_violated:
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 8. Фиксация снимка плана и проверка перед удалением
                snapshot = compute_storage_snapshot(stand_root, report, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)
                if allow_interruption_hook and hasattr(execute_stand_cleanup, "_pre_verify_hook"):
                    try:
                        execute_stand_cleanup._pre_verify_hook()
                    except Exception as e:
                        _log(f"pre_verify_hook вызвал исключение: {e}")

                snap_ok, snap_err = verify_storage_snapshot(stand_root, snapshot, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)
                if not snap_ok:
                    _log(f"отмена плана: обнаружено параллельное изменение метаданных ({snap_err})")
                    arch_entry["status"] = "aborted"
                    arch_entry["reason"] = f"metadata_snapshot_mismatch: {snap_err}"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                if dry_run:
                    _log(f"[DRY-RUN] запланировано к удалению кандидатов: {len(candidates)}")
                    arch_entry["status"] = "completed"
                    arch_entry["reason"] = "dry_run_success"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 9. Исполнение удаления кандидатов для текущего архива
                cand_status_list = [
                    {
                        "name": c["name"],
                        "id": str(c.get("id") or (drive_test_registry.get(c["name"], {}).get("id") if drive_test_mode and drive_test_registry else "") or ""),
                        "size": c.get("size", 0),
                        "reason": c.get("reason", "proven_candidate"),
                        "state": "SKIPPED_UNEXECUTED",
                    }
                    for c in candidates
                ]
                arch_entry["candidates"] = cand_status_list
                sync_run_report_totals(run_report)
                flush_atomic_report(report_path, run_report)

                stop_reason = None
                deleted_arch = []

                for idx, cand in enumerate(candidates):
                    fname = cand["name"]

                    # Перед отправкой запроса фиксируем состояние UNCONFIRMED_UNKNOWN
                    cand_status_list[idx]["state"] = "UNCONFIRMED_UNKNOWN"
                    cand_status_list[idx]["requested_at"] = time.time()
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)

                    # Тестовый хук прерывания/ошибки
                    if allow_interruption_hook and hasattr(execute_stand_cleanup, "_test_hook"):
                        try:
                            execute_stand_cleanup._test_hook(fname, idx)
                        except Exception as e:
                            stop_reason = f"interrupted: {e}"
                            _log(f"прерывание прохода очистки на объекте {fname}: {e}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_UNKNOWN"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                    if is_remote:
                        if drive_test_mode:
                            reg_info = drive_test_registry.get(fname, {})
                            cand_fid = reg_info.get("id") if isinstance(reg_info, dict) else None
                        else:
                            cand_fid = cand.get("id") or cand.get("ID")
                            reg_info = {"id": cand_fid, "name": fname, "size": cand.get("size", 0)}

                        f_size = cand.get("size") or (reg_info.get("size") if isinstance(reg_info, dict) else 0)
                        del_ok = False
                        del_err = None
                        if drive_adapter:
                            del_ok, del_err = drive_adapter.delete_candidate(fname, reg_info)
                        else:
                            stop_reason = f"drive_adapter is required for Drive deletion without name fallback: {fname}"
                            _log(f"ошибка: {stop_reason}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_ERROR"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                        fate_ok = False
                        if del_ok:
                            fate_ok = drive_adapter.verify_deleted(fname, reg_info)

                        if not del_ok or not fate_ok:
                            stop_reason = f"error deleting or verifying candidate {fname} by file ID {cand_fid}: {del_err or 'candidate_fate_unverified'}"
                            _log(f"ошибка при удалении или проверке судьбы {fname}: {stop_reason}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_ERROR"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                        cand_status_list[idx]["state"] = "CONFIRMED_DELETED"
                        cand_status_list[idx]["deleted_at"] = time.time()
                        sync_run_report_totals(run_report)
                        flush_atomic_report(report_path, run_report)

                        deleted_arch.append({
                            "name": fname,
                            "reason": cand.get("reason", "proven_candidate"),
                            "size": f_size,
                            "id": str(cand_fid),
                            "state": "CONFIRMED_DELETED",
                        })
                        storage_label = "Google Drive (тестовая папка)" if drive_test_mode else "Google Drive"
                        _log(f"удаление в {storage_label} {fname} (file ID {cand_fid}): {cand.get('reason')} ({f_size} байт)")
                    else:
                        target_file = stand_root / fname
                        try:
                            f_size = 0
                            if target_file.exists():
                                f_size = target_file.stat().st_size
                            else:
                                f_size = cand.get("size", 0)

                            if drive_adapter is not None:
                                cand_info = {"name": fname, "size": f_size, "id": str(target_file)}
                                del_ok, del_err = drive_adapter.delete_candidate(fname, cand_info)
                                if not del_ok:
                                    raise OSError(del_err or "adapter_deletion_failed")
                                if hasattr(drive_adapter, "verify_deleted"):
                                    if not drive_adapter.verify_deleted(fname, cand_info):
                                        raise OSError("adapter_verify_deleted_failed")
                            else:
                                if target_file.exists():
                                    target_file.unlink()

                            cand_status_list[idx]["state"] = "CONFIRMED_DELETED"
                            cand_status_list[idx]["deleted_at"] = time.time()
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)

                            deleted_arch.append({
                                "name": fname,
                                "reason": cand.get("reason", "proven_candidate"),
                                "size": f_size,
                                "state": "CONFIRMED_DELETED",
                            })
                            _log(f"удаление {fname}: {cand.get('reason')} ({f_size} байт)")
                        except OSError as e:
                            stop_reason = f"error deleting {fname}: {e}"
                            _log(f"ошибка при удалении файла {fname}: {e}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_ERROR"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                arch_deleted_bytes = sum(f["size"] for f in deleted_arch)
                arch_entry["deleted_bytes"] = arch_deleted_bytes
                if not stop_reason:
                    arch_entry["status"] = "completed"
                sync_run_report_totals(run_report)
                flush_atomic_report(report_path, run_report)

            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(lock_fd)
                except OSError:
                    pass

    finally:
        try:
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)
            if old_int is not None:
                signal.signal(signal.SIGINT, old_int)
        except (ValueError, AttributeError):
            pass

    sync_run_report_totals(run_report)
    flush_atomic_report(report_path, run_report)

    if len(target_objects) == 1:
        single_bname = target_objects[0][:-8] if target_objects[0].endswith(".tar.zst") else target_objects[0]
        arch_res = run_report["archives"].get(single_bname, {})
        return {
            "status": arch_res.get("status", run_report["status"]),
            "reason": arch_res.get("reason", run_report.get("reason")),
            "deleted": arch_res.get("deleted", []),
            "deleted_bytes": arch_res.get("deleted_bytes", 0),
            "remaining": [c for c in arch_res.get("candidates", []) if c.get("state") != "CONFIRMED_DELETED"],
            "report": arch_res.get("classification_report", {}),
            "log_messages": log_messages,
            "candidate_states": arch_res.get("candidates", []),
            "deleted_count": arch_res.get("deleted_count", 0),
            "unconfirmed_count": arch_res.get("unconfirmed_count", 0),
            "skipped_count": arch_res.get("skipped_count", 0),
            "archives": run_report["archives"],
            "run_id": run_report.get("run_id"),
            "object": target_objects[0],
            "total_candidates": run_report.get("total_candidates", 0),
        }

    res = dict(run_report)
    res["log_messages"] = log_messages
    res["candidate_states"] = run_report.get("candidates", [])
    res["remaining"] = [c for c in run_report.get("candidates", []) if c.get("state") != "CONFIRMED_DELETED"]
    return res


def format_cleanup_result(result: dict) -> str:
    """Форматирует результат выполнения очистки в текстовый журнал."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"РЕЗУЛЬТАТ СТЕНДОВОЙ ОЧИСТКИ: статус {result.get('status', 'unknown').upper()}")
    if result.get("reason"):
        lines.append(f"Причина / пояснение: {result.get('reason')}")
    lines.append("-" * 70)

    archives = result.get("archives", {})
    if archives:
        lines.append(f"Обработано архивов: {len(archives)}")
        for aname, adata in archives.items():
            lines.append(f"  • {aname}: статус {adata.get('status')} (удалено: {adata.get('deleted_count', 0)}, не подтверждено: {adata.get('unconfirmed_count', 0)}, пропущено: {adata.get('skipped_count', 0)})")
        lines.append("-" * 70)

    deleted = result.get("deleted", [])
    lines.append(f"Всего удалено кандидатов ({len(deleted)} файлов):")
    if deleted:
        for f in deleted:
            lines.append(f"  • {f['name']} — основание: {f.get('reason', 'proven_candidate')} ({f.get('size', 0) >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"Локальный объём удалённых файлов: {result.get('deleted_bytes', 0)} байт")
    lines.append("  (Внимание: значение получено на локальном стенде и не является измеренным освобождением квоты Google Drive)")

    remaining = result.get("remaining", [])
    if remaining:
        lines.append("-" * 70)
        lines.append(f"Оставшиеся неудалённые кандидаты ({len(remaining)} файлов):")
        for f in remaining:
            lines.append(f"  • {f.get('name')} — {f.get('reason')}")

    lines.append("=" * 70)
    return "\n".join(lines)


def main(args):
    if len(args) < 3:
        print("использование: wolf_cleaner.py STAND_DIR OBJECT... [--grant-file FILE] [--lock-dir DIR] [--dry-run] [--json] [--drive-test] [--drive-folder-id ID] [--registry-file FILE] [--enabled] [--report-file FILE] [--access-token TOKEN]", file=sys.stderr)
        return 2

    stand_dir = args[1]
    objects = []
    flags = []
    i = 2
    flag_keys_with_arg = {
        "--grant-file", "--lock-dir", "--tmp-dir", "--state-dir",
        "--run-id", "--iid", "--folder-id", "--drive-folder-id",
        "--report-file", "--access-token", "--registry-file", "--clean-pass-id",
    }
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            flags.append(arg)
            if arg in flag_keys_with_arg and i + 1 < len(args):
                flags.append(args[i + 1])
                i += 1
        else:
            objects.append(arg)
        i += 1

    if not objects:
        print("ошибка: не указаны объекты для очистки", file=sys.stderr)
        return 2

    json_mode = "--json" in flags
    dry_run = "--dry-run" in flags
    drive_test_mode = "--drive-test" in flags

    grant_file = "/run/wolf/cleanup_grant.json"
    lock_dir = "/run/wolf"
    tmp_dir = "/var/tmp/wolf"
    state_dir = "/var/lib/wolf"
    run_id_val = None
    iid_val = None
    folder_id_val = None
    drive_test_folder_id = None
    drive_test_registry = None
    report_file = None
    access_token = None
    clean_pass_id = None

    for i, flag in enumerate(flags):
        if flag == "--grant-file" and i + 1 < len(flags):
            grant_file = flags[i + 1]
        elif flag == "--lock-dir" and i + 1 < len(flags):
            lock_dir = flags[i + 1]
        elif flag == "--tmp-dir" and i + 1 < len(flags):
            tmp_dir = flags[i + 1]
        elif flag == "--state-dir" and i + 1 < len(flags):
            state_dir = flags[i + 1]
        elif flag == "--run-id" and i + 1 < len(flags):
            run_id_val = flags[i + 1]
        elif flag == "--iid" and i + 1 < len(flags):
            iid_val = flags[i + 1]
        elif flag == "--folder-id" and i + 1 < len(flags):
            folder_id_val = flags[i + 1]
        elif flag == "--drive-folder-id" and i + 1 < len(flags):
            drive_test_folder_id = flags[i + 1]
        elif flag == "--report-file" and i + 1 < len(flags):
            report_file = flags[i + 1]
        elif flag == "--access-token" and i + 1 < len(flags):
            access_token = flags[i + 1]
        elif flag == "--clean-pass-id" and i + 1 < len(flags):
            clean_pass_id = flags[i + 1]
        elif flag == "--registry-file" and i + 1 < len(flags):
            reg_path = Path(flags[i + 1])
            if reg_path.is_file():
                try:
                    drive_test_registry = json.loads(reg_path.read_text("utf-8"))
                except Exception:
                    pass

    grant_data = None
    if os.path.isfile(grant_file):
        try:
            with open(grant_file, "r", encoding="utf-8") as gf:
                grant_data = json.load(gf)
        except Exception:
            pass

    enabled_val = True if "--enabled" in flags else (False if "--disabled" in flags else None)
    if enabled_val is None:
        enabled_val = is_cleanup_enabled(grant_data=grant_data, lock_dir=lock_dir)

    res = execute_stand_cleanup(
        stand_dir=stand_dir,
        obj_name=objects if len(objects) > 1 else objects[0],
        grant_file=grant_file,
        lock_dir=lock_dir,
        tmp_dir=tmp_dir,
        state_dir=state_dir,
        run_id_val=run_id_val,
        iid_val=iid_val,
        folder_id_val=folder_id_val,
        dry_run=dry_run,
        drive_test_mode=drive_test_mode,
        drive_test_folder_id=drive_test_folder_id,
        drive_test_registry=drive_test_registry,
        drive_production_mode=(not drive_test_mode and ":" in str(stand_dir)),
        enabled=enabled_val,
        report_file=report_file,
        access_token=access_token,
        require_isolation=("--require-isolation" in flags),
        clean_pass_id=clean_pass_id,
    )

    if json_mode:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_cleanup_result(res))

    return 0 if res.get("status") in ("completed", "noop", "disabled") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

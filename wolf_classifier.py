"""wolf_classifier.py — классификатор объектов облачного хранилища VastGame.

Реализует безопасный аудит и предварительный просмотр (preview/view-only):
- Категория 1 (ACTIVE): подтверждённые активные данные — безусловно сохранить;
- Группа Б (PROTECTED): незавершённые, staged, pending и неподтверждённые попытки — защитить;
- Группа А (CANDIDATE): доказанно вытесненные объекты, недостижимые в коде unpack — кандидаты;
- Группа В (AMBIGUOUS): повреждённые оглавления, ошибки формата и неизвестные файлы — запретить очистку.

Категории взаимоисключающие; приоритет защиты всегда превалирует.
Никакого удаления или перемещения файлов этот модуль не производит.
Модуль работает строго в режиме чтения (read-only).
"""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

PARTS_HEAD = "vastgame-parts 1"
STAGE_HEAD = "vastgame-stage 1"


def part_re(obj):
    return re.compile(re.escape(obj) + r"\.g([0-9a-f]{8})-\d{4}")


def delta_pattern(base_tar):
    return re.compile(r"^(" + re.escape(base_tar) + r"\.([0-9a-f]{8}))(.*)$")


def parse_parts_index_text(text, obj):
    """Парсинг и строгая валидация содержимого .parts оглавления.
    Возвращает (gen, total_size, parts_list). При ошибке синтаксиса выбрасывает ValueError."""
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != PARTS_HEAD:
        raise ValueError(f"{obj}: неверный заголовок оглавления: {lines[0] if lines else 'пусто'}")
    try:
        total = int(lines[1].strip())
    except Exception as e:
        raise ValueError(f"{obj}: неверный размер в оглавлении: {lines[1] if len(lines) > 1 else ''}") from e

    pr = part_re(obj)
    parts = []
    gen = None
    for i, line in enumerate(lines[2:], 3):
        if not line.strip():
            continue
        if "\t" in line:
            row = [x.strip() for x in line.split("\t")]
        else:
            row = line.split()
        if len(row) != 3:
            raise ValueError(f"{obj}: строка {i} оглавления повреждена: {line!r}")
        name, md5_val, size_str = row
        m = pr.fullmatch(name)
        if not m:
            raise ValueError(f"{obj}: строка {i}: имя части {name!r} не соответствует шаблону")
        p_gen = m.group(1)
        if gen is None:
            gen = p_gen
        elif gen != p_gen:
            raise ValueError(f"{obj}: в оглавлении смешаны поколения {gen} и {p_gen}")
        try:
            sz = int(size_str)
        except Exception as e:
            raise ValueError(f"{obj}: строка {i}: неверный размер {size_str}") from e
        parts.append((name, md5_val, sz))

    if not parts:
        raise ValueError(f"{obj}: оглавление не содержит частей")
    sum_sz = sum(p[2] for p in parts)
    if sum_sz != total:
        raise ValueError(f"{obj}: сумма размеров частей ({sum_sz}) не совпадает с итогом ({total})")
    return gen, total, parts


def parse_receipt(text, obj=None):
    """Строгий разбор квитанции подготовки vastgame-stage 1 (совместим с wolf-parts.py)."""
    lines = text.splitlines()
    if len(lines) < 4 or lines[0].strip() != STAGE_HEAD:
        raise ValueError("нет квитанции подготовки: неверный заголовок")
    kind = lines[1].strip()
    if kind == "single":
        return ("single", lines[2].strip(), int(lines[3].strip()))
    if kind != "parts" or len(lines) < 6:
        raise ValueError("нет квитанции подготовки: неверный формат parts")
    parts = []
    for i, ln in enumerate(lines[6:], 7):
        if not ln.strip():
            continue
        row = ln.split("\t")
        if len(row) != 3:
            raise ValueError(f"повреждённая строка части в квитанции (строка {i}): {ln!r}")
        n, m, s = row
        if obj and not n.startswith(obj + ".g"):
            raise ValueError(f"чужое имя в квитанции: {n} (ожидался префикс {obj}.g)")
        parts.append((n, m, int(s)))
    return ("parts", lines[2].strip(), lines[3].strip(), int(lines[4].strip()), parts, float(lines[5].strip()))


def read_attempt_file(path, expected_obj=None):
    """Чтение и валидация файла маркера попытки attempt-* из $T."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path}: пустой файл attempt")
    kind = lines[0].strip()
    if not kind:
        raise ValueError(f"{path}: пустой заголовок kind в attempt")
    stage_text = "\n".join(lines[1:])
    receipt = parse_receipt(stage_text, obj=expected_obj)
    return kind, receipt


def read_pending_file(path):
    """Чтение и валидация постоянного файла квитанции $S/pending/<архив>."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path}: пустой файл pending")
    first_line = lines[0].strip()
    if not first_line.startswith("object "):
        raise ValueError(f"{path}: первая строка должна начинаться с 'object ': {first_line!r}")
    target_obj = first_line[7:].strip()
    if not target_obj:
        raise ValueError(f"{path}: пустое значение object в pending")
    stage_text = "\n".join(lines[1:])
    receipt = parse_receipt(stage_text, obj=target_obj)
    return target_obj, receipt


def _read_remote_file(remote, rel_name, rclone_cmd="rclone"):
    """Чтение содержимого файла из remote (локального каталога или через rclone)."""
    if os.path.isdir(remote):
        p = Path(remote) / rel_name
        if not p.is_file():
            return None
        try:
            return p.read_bytes()
        except OSError:
            return None
    try:
        res = subprocess.run(
            [rclone_cmd, "cat", f"{remote.rstrip('/')}/{rel_name}"],
            capture_output=True,
            timeout=15,
        )
        if res.returncode == 0:
            return res.stdout
        return None
    except Exception:
        return None


def _get_remote_listing(remote, rclone_cmd="rclone"):
    """Получение описи файлов в remote со свойствами (Name, Size, ModTime, MD5)."""
    if os.path.isdir(remote):
        items = []
        import hashlib
        root = Path(remote)
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            try:
                st = p.stat()
                mtime_iso = datetime.fromtimestamp(st.st_mtime).isoformat()
                h = hashlib.md5()
                with open(p, "rb") as f:
                    while b := f.read(65536):
                        h.update(b)
                md5_val = h.hexdigest()
                items.append({
                    "Name": rel,
                    "Size": st.st_size,
                    "ModTime": mtime_iso,
                    "ModTime_epoch": st.st_mtime,
                    "MD5": md5_val,
                })
            except OSError:
                continue
        return items

    # Через rclone lsjson
    try:
        res = subprocess.run(
            [rclone_cmd, "lsjson", "-M", "--hash", remote],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0:
            data = json.loads(res.stdout or "[]")
            items = []
            for d in data:
                if d.get("IsDir"):
                    continue
                name = d.get("Name") or ""
                sz = d.get("Size") or 0
                mtime = d.get("ModTime") or ""
                epoch = 0.0
                if mtime:
                    try:
                        ts_str = re.sub(r"(\.\d{6})\d+", r"\1", mtime).replace("Z", "+00:00")
                        epoch = datetime.fromisoformat(ts_str).timestamp()
                    except Exception:
                        pass
                hashes = d.get("Hashes") or {}
                md5_val = hashes.get("md5") or hashes.get("MD5") or ""
                items.append({
                    "Name": name,
                    "Size": sz,
                    "ModTime": mtime,
                    "ModTime_epoch": epoch,
                    "MD5": md5_val,
                    "ID": d.get("ID") or "",
                })
            return items
        return None
    except Exception:
        return None


def _check_session_authorization(
    grant_file="/run/wolf/cleanup_grant.json",
    run_id_val=None,
    iid_val=None,
    folder_id_val=None,
):
    """Проверка допуска сессии строго через единую библиотеку wolf_session.
    Отсутствие модуля wolf_session или любая ошибка проверки означает отказ (False).
    Никаких упрощённых fallback-проверок без сверки run_id, iid и folder_id."""
    try:
        from wolf_session import is_cleanup_authorized
        return is_cleanup_authorized(
            grant_file=grant_file,
            run_id_val=run_id_val,
            iid_val=iid_val,
            folder_id_val=folder_id_val,
        )
    except Exception:
        return False


def classify_archive(
    remote,
    obj_name,
    listing=None,
    grant_file="/run/wolf/cleanup_grant.json",
    tmp_dir="/var/tmp/wolf",
    state_dir="/var/lib/wolf",
    rclone_cmd="rclone",
    run_id_val=None,
    iid_val=None,
    folder_id_val=None,
):
    """Классифицирует все облачные файлы, относящиеся к указанному архиву.

    Возвращает словарь отчёта с гарантией non-overlapping категорий:
    - ACTIVE: подтверждённые активные данные (оглавления и части, активный одиночный архив);
    - PROTECTED: незавершённые выгрузки, неподтверждённые поколения, объекты попыток/staged;
    - CANDIDATE: доказанно вытесненные объекты (независимо от возраста/mtime);
    - AMBIGUOUS: ошибки синтаксиса, посторонние файлы или повреждённые квитанции.
    """
    # Нормализация имени объекта
    obj_str = str(obj_name).strip()
    if obj_str.endswith(".tar.zst"):
        base_tar = obj_str
        base_name = obj_str[:-8]
    else:
        base_name = obj_str
        base_tar = f"{obj_str}.tar.zst"

    report = {
        "object": base_tar,
        "base_name": base_name,
        "active_base_kind": None,
        "active_base_version": None,
        "active_delta_kind": None,
        "active_delta_version": None,
        "cleanup_allowed": False,
        "cleanup_block_reason": None,
        "summary": {
            "total_files": 0,
            "active_count": 0,
            "protected_count": 0,
            "candidate_count": 0,
            "ambiguous_count": 0,
        },
        "files": [],
    }

    # 1. Получение описи хранилища
    if listing is None:
        listing = _get_remote_listing(remote, rclone_cmd=rclone_cmd)

    if listing is None:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "storage_listing_failed"
        return report

    # 2. Фильтрация файлов, относящихся к данному архиву
    by_name = {}
    d_pat = delta_pattern(base_tar)
    pr_base = part_re(base_tar)

    for item in listing:
        fname = item["Name"]
        if fname in (base_tar, f"{base_tar}.parts", f"{base_tar}.parts.new", f"{base_tar}.part") or pr_base.fullmatch(fname):
            by_name[fname] = item
            continue
        m = d_pat.match(fname)
        if m:
            by_name[fname] = item
            continue
        if fname.startswith(f"{base_name}.") or fname.startswith(f"{base_tar}."):
            by_name[fname] = item

    if not by_name:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "archive_not_found_in_storage"
        return report

    report["summary"]["total_files"] = len(by_name)

    # 3. Чтение квитанций попыток (attempt-* и $S/pending/<архив>)
    # Отслеживаем повреждения квитанций: любая ошибка чтения/парсинга блокирует очистку!
    receipt_errors = []
    protected_targets = set()       # target_obj (base_tar или delta), описанные квитанциями
    protected_part_names = set()    # конкретные имена файлов частей из квитанций
    protected_generations = {}      # target_obj -> set(gen_strings)

    # 3.1. Сканирование tmp_dir (attempt-*)
    # ВНИМАНИЕ: движок сохраняет файл как os.path.join(TMP, "attempt-" + obj.replace("/", "_")).
    # Для сопоставления используем прямое преобразование известного имени base_tar и его дельт,
    # исключая неоднозначное обратное replace("_", "/").
    if tmp_dir and os.path.isdir(tmp_dir):
        base_att_name = "attempt-" + base_tar.replace("/", "_")
        try:
            for entry in os.listdir(tmp_dir):
                if entry == base_att_name:
                    target_obj = base_tar
                elif entry.startswith(base_att_name + "."):
                    delta_suffix = entry[len(base_att_name) + 1:]
                    target_obj = f"{base_tar}.{delta_suffix}"
                else:
                    continue

                att_path = os.path.join(tmp_dir, entry)
                if not os.path.isfile(att_path):
                    continue
                try:
                    _, receipt = read_attempt_file(att_path, expected_obj=target_obj)
                    protected_targets.add(target_obj)
                    if receipt[0] == "parts":
                        gen = receipt[1]
                        protected_generations.setdefault(target_obj, set()).add(gen)
                        for pn, _, _ in receipt[4]:
                            protected_part_names.add(pn)
                except Exception as e:
                    receipt_errors.append((att_path, str(e)))
        except OSError as e:
            receipt_errors.append((tmp_dir, str(e)))

    # 3.2. Сканирование state_dir/pending/<архив>
    if state_dir and os.path.isdir(state_dir):
        p_dir = os.path.join(state_dir, "pending")
        if os.path.isdir(p_dir):
            checked_paths = set()
            for cand_name in (base_name, base_tar):
                cand_path = os.path.join(p_dir, cand_name)
                if os.path.isfile(cand_path) and cand_path not in checked_paths:
                    checked_paths.add(cand_path)
                    try:
                        target_obj, receipt = read_pending_file(cand_path)
                        protected_targets.add(target_obj)
                        if receipt[0] == "parts":
                            gen = receipt[1]
                            protected_generations.setdefault(target_obj, set()).add(gen)
                            for pn, _, _ in receipt[4]:
                                protected_part_names.add(pn)
                    except Exception as e:
                        receipt_errors.append((cand_path, str(e)))

    # 3.3. Сканирование staged файлов хранилища (.parts.new и .part)
    for fname in by_name:
        if fname.endswith(".parts.new"):
            t_obj = fname[:-10]
            protected_targets.add(t_obj)
            s_bytes = _read_remote_file(remote, fname, rclone_cmd=rclone_cmd)
            if s_bytes:
                try:
                    s_gen, _, s_parts = parse_parts_index_text(s_bytes.decode("utf-8", "replace"), t_obj)
                    protected_generations.setdefault(t_obj, set()).add(s_gen)
                    for pn, _, _ in s_parts:
                        protected_part_names.add(pn)
                except Exception:
                    pass
        elif fname.endswith(".part"):
            t_obj = fname[:-5]
            protected_targets.add(t_obj)

    # 4. Анализ базового архива (parts vs single)
    active_base_parts_item = by_name.get(f"{base_tar}.parts")
    active_base_single_item = by_name.get(base_tar)
    active_base_parts_list = []
    active_base_gen = None
    active_base_md5 = None
    base_is_corrupted = False

    if active_base_parts_item:
        idx_bytes = _read_remote_file(remote, f"{base_tar}.parts", rclone_cmd=rclone_cmd)
        if idx_bytes is None:
            base_is_corrupted = True
        else:
            try:
                gen, _, p_list = parse_parts_index_text(idx_bytes.decode("utf-8", "replace"), base_tar)
                active_base_gen = gen
                active_base_parts_list = p_list
                import hashlib
                active_base_md5 = hashlib.md5(idx_bytes).hexdigest()
                report["active_base_kind"] = "parts"
                report["active_base_version"] = active_base_md5
            except Exception:
                base_is_corrupted = True
                report["active_base_kind"] = "corrupted_parts"
    elif active_base_single_item:
        active_base_md5 = active_base_single_item.get("MD5") or ""
        report["active_base_kind"] = "single"
        report["active_base_version"] = active_base_md5

    # 5. Анализ активной накопительной дельты
    active_delta_base = None
    active_delta_parts_item = None
    active_delta_single_item = None
    active_delta_parts_list = []
    active_delta_gen = None
    active_delta_md5 = None

    if active_base_md5:
        delta_prefix = f"{base_tar}.{active_base_md5[:8]}"
        active_delta_base = delta_prefix
        active_delta_parts_item = by_name.get(f"{delta_prefix}.parts")
        active_delta_single_item = by_name.get(delta_prefix)

        if active_delta_parts_item:
            d_idx_bytes = _read_remote_file(remote, f"{delta_prefix}.parts", rclone_cmd=rclone_cmd)
            if d_idx_bytes:
                try:
                    d_gen, _, d_p_list = parse_parts_index_text(d_idx_bytes.decode("utf-8", "replace"), delta_prefix)
                    active_delta_gen = d_gen
                    active_delta_parts_list = d_p_list
                    import hashlib
                    active_delta_md5 = hashlib.md5(d_idx_bytes).hexdigest()
                    report["active_delta_kind"] = "parts"
                    report["active_delta_version"] = active_delta_md5
                except Exception:
                    pass
        elif active_delta_single_item:
            active_delta_md5 = active_delta_single_item.get("MD5") or ""
            report["active_delta_kind"] = "single"
            report["active_delta_version"] = active_delta_md5

    # 6. Классификация каждого файла
    classified_files = []
    active_base_part_names = set(p[0] for p in active_base_parts_list)
    active_delta_part_names = set(p[0] for p in active_delta_parts_list)

    for fname, item in sorted(by_name.items()):
        f_size = item.get("Size") or 0
        f_mtime = item.get("ModTime") or ""
        f_md5 = item.get("MD5") or ""
        f_id = str(item.get("ID") or item.get("id") or "")

        # Проверка соответствия форматам VastGame
        is_known_format = (
            fname in (base_tar, f"{base_tar}.parts", f"{base_tar}.parts.new", f"{base_tar}.part")
            or bool(pr_base.fullmatch(fname))
            or bool(d_pat.match(fname))
        )
        if not is_known_format:
            classified_files.append({
                "name": fname,
                "category": "AMBIGUOUS",
                "reason": "unrecognized_archive_file_pattern_or_grammar_violation",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай А: повреждённое оглавление активной базы
        if base_is_corrupted:
            if fname == f"{base_tar}.parts":
                category = "AMBIGUOUS"
                reason = "corrupted_parts_index_syntax_or_checksum_error"
            else:
                category = "PROTECTED"
                reason = "active_base_corrupted_cleanup_aborted_for_safety"
            classified_files.append({
                "name": fname,
                "category": category,
                "reason": reason,
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Б: Активная база
        if active_base_parts_item:
            if fname == f"{base_tar}.parts":
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "active_base_parts_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if fname in active_base_part_names:
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "part_of_active_base_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
        elif active_base_single_item and fname == base_tar:
            classified_files.append({
                "name": fname,
                "category": "ACTIVE",
                "reason": "active_base_single_archive",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай В: Активная дельта
        if active_delta_base:
            if active_delta_parts_item and fname == f"{active_delta_base}.parts":
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "active_delta_parts_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if fname in active_delta_part_names:
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "part_of_active_delta_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if active_delta_single_item and fname == active_delta_base:
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "active_delta_single_archive",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue

        # Случай Г: Файлы незавершённой подготовки (staged / partial / in-flight)
        if fname.endswith(".part"):
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "partial_upload_in_progress",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        if fname.endswith(".parts.new"):
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "staged_index_upload_in_progress",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Д: Файлы, прямо защищённые квитанциями попыток (attempt-* или pending)
        if fname in protected_part_names:
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "pending_attempt_part_matching_receipt",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Е: Части базового архива (не входящие в активное оглавление)
        m_part = pr_base.fullmatch(fname)
        if m_part:
            p_gen = m_part.group(1)
            # Проверяем принадлежность к поколению попытки или staged
            if p_gen in protected_generations.get(base_tar, set()):
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "pending_attempt_generation_matching_receipt",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue

            # Неподтверждённое поколение (gen != active_base_gen):
            # Внимание: mtime (старое, равное или новое) НЕ доказывает возможность удаления!
            # Без строгого подтверждения вытеснения любая такая часть остаётся PROTECTED.
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "unconfirmed_generation_without_proven_displacement",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Ж: Одиночный архив базы рядом с активным .parts оглавлением
        if fname == base_tar and active_base_parts_item:
            if base_tar in protected_targets:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "pending_attempt_single_archive_matching_receipt",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if receipt_errors:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "corrupted_receipt_blocks_cleanup_candidate_protection",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            # Структурно вытесненная одиночная база (в unpack/rver используется активное .parts оглавление)
            classified_files.append({
                "name": fname,
                "category": "CANDIDATE",
                "reason": "displaced_single_archive_superseded_by_active_parts_index",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
                "id": f_id,
            })
            continue

        # Случай З: Дельты (накопительные дельты к старым базам)
        m_delta = d_pat.match(fname)
        if m_delta:
            d_prefix = m_delta.group(1)
            d_hash = m_delta.group(2)
            d_suffix = m_delta.group(3)

            # Проверяем защиту по квитанциям и staged маркерам:
            # Неподтверждённую попытку дельты нельзя объявлять мусором только из-за несовпадения её базы!
            delta_is_protected = False
            if d_prefix in protected_targets or fname in protected_part_names:
                delta_is_protected = True
            else:
                m_dpart = part_re(d_prefix).fullmatch(fname)
                if m_dpart and m_dpart.group(1) in protected_generations.get(d_prefix, set()):
                    delta_is_protected = True

            if delta_is_protected:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "pending_attempt_or_staged_delta_protected",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                    "id": f_id,
                })
                continue

            if receipt_errors:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "corrupted_receipt_blocks_cleanup_candidate_protection",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                    "id": f_id,
                })
                continue

            # Если дельта относится к чужой (старой) базе (hash != h_base[:8]):
            if not active_base_md5 or d_hash != active_base_md5[:8]:
                if d_suffix == "":
                    classified_files.append({
                        "name": fname,
                        "category": "CANDIDATE",
                        "reason": "displaced_delta_superseded_by_newer_base",
                        "size": f_size,
                        "md5": f_md5,
                        "mtime": f_mtime,
                        "id": f_id,
                    })
                    continue
                if d_suffix == ".parts":
                    classified_files.append({
                        "name": fname,
                        "category": "CANDIDATE",
                        "reason": "displaced_delta_parts_index_superseded_by_newer_base",
                        "size": f_size,
                        "md5": f_md5,
                        "mtime": f_mtime,
                        "id": f_id,
                    })
                    continue
                pr_d = part_re(d_prefix)
                if pr_d.fullmatch(fname):
                    classified_files.append({
                        "name": fname,
                        "category": "CANDIDATE",
                        "reason": "displaced_delta_part_superseded_by_newer_base",
                        "size": f_size,
                        "md5": f_md5,
                        "mtime": f_mtime,
                        "id": f_id,
                    })
                    continue

        # Случай И: Любой иной нераспознанный файл
        classified_files.append({
            "name": fname,
            "category": "AMBIGUOUS",
            "reason": "unrecognized_archive_file_pattern_or_grammar_violation",
            "size": f_size,
            "md5": f_md5,
            "mtime": f_mtime,
        })

    report["files"] = classified_files

    # 7. Подсчёт итогов
    n_act = sum(1 for f in classified_files if f["category"] == "ACTIVE")
    n_prot = sum(1 for f in classified_files if f["category"] == "PROTECTED")
    n_cand = sum(1 for f in classified_files if f["category"] == "CANDIDATE")
    n_amb = sum(1 for f in classified_files if f["category"] == "AMBIGUOUS")

    report["summary"]["active_count"] = n_act
    report["summary"]["protected_count"] = n_prot
    report["summary"]["candidate_count"] = n_cand
    report["summary"]["ambiguous_count"] = n_amb

    # 8. Проверка допуска к очистке
    has_auth = _check_session_authorization(
        grant_file=grant_file,
        run_id_val=run_id_val,
        iid_val=iid_val,
        folder_id_val=folder_id_val,
    )

    if receipt_errors:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = f"corrupted_receipt_or_pending_attempt: {receipt_errors[0][0]}"
    elif n_amb > 0:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "has_ambiguous_or_corrupted_objects"
    elif n_cand == 0:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "no_candidates_to_clean"
    elif not has_auth:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "view_only_no_session_grant"
    else:
        report["cleanup_allowed"] = True
        report["cleanup_block_reason"] = None

    return report


def format_classification_report(report):
    """Форматирование отчёта в человекочитаемый текстовый вид без секретов."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"ОТЧЁТ КЛАССИФИКАЦИИ ОБЛАЧНОГО ОБЪЕКТА: {report['object']}")
    act_ver = report.get("active_base_version") or "нет"
    if report.get("active_delta_version"):
        act_ver += f" + {report['active_delta_version'][:8]} (дельта)"
    lines.append(f"Активная версия: {act_ver}")

    if report["cleanup_allowed"]:
        clean_status = "РАЗРЕШЕНО (сессионное разрешение активно, есть доказанные кандидаты)"
    else:
        reason_map = {
            "view_only_no_session_grant": "ЗАПРЕЩЕНО (режим VIEW-ONLY: отсутствует сессионное разрешение)",
            "no_candidates_to_clean": "ЗАПРЕЩЕНО (нет кандидатов на очистку)",
            "has_ambiguous_or_corrupted_objects": "ЗАПРЕЩЕНО (обнаружены повреждённые или сомнительные объекты)",
            "storage_listing_failed": "ЗАПРЕЩЕНО (сбой получения списка файлов хранилища)",
            "archive_not_found_in_storage": "ЗАПРЕЩЕНО (архив не найден в хранилище)",
        }
        raw_reason = str(report.get("cleanup_block_reason") or "")
        if raw_reason.startswith("corrupted_receipt_or_pending_attempt"):
            clean_status = f"ЗАПРЕЩЕНО (обнаружена повреждённая квитанция: {raw_reason.split(': ', 1)[-1]})"
        else:
            clean_status = reason_map.get(raw_reason, f"ЗАПРЕЩЕНО ({raw_reason})")
    lines.append(f"Статус очистки: {clean_status}")
    lines.append("-" * 70)

    # Группировка
    groups = {"ACTIVE": [], "PROTECTED": [], "CANDIDATE": [], "AMBIGUOUS": []}
    for f in report.get("files", []):
        groups[f["category"]].append(f)

    # Вывод по категориям
    lines.append(f"[АКТИВНЫЕ] ({len(groups['ACTIVE'])} файлов) — абсолютная неприкосновенность:")
    if groups["ACTIVE"]:
        for f in groups["ACTIVE"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"[ЗАЩИЩЁННЫЕ] ({len(groups['PROTECTED'])} файлов) — незавершённые или требующие подтверждения:")
    if groups["PROTECTED"]:
        for f in groups["PROTECTED"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"[КАНДИДАТЫ] ({len(groups['CANDIDATE'])} файлов) — доказанно вытесненные объекты:")
    if groups["CANDIDATE"]:
        for f in groups["CANDIDATE"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"[СОМНИТЕЛЬНЫЕ / ОШИБКИ] ({len(groups['AMBIGUOUS'])} файлов) — очистка прерывается:")
    if groups["AMBIGUOUS"]:
        for f in groups["AMBIGUOUS"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append("-" * 70)
    lines.append("ИТОГ: удаление или перемещение файлов НЕ производится (только аудит).")
    lines.append("=" * 70)
    return "\n".join(lines)


def main(args):
    import sys
    if len(args) < 3:
        print("использование: wolf_classifier.py REMOTE OBJECT [--json] [--tmp-dir DIR] [--state-dir DIR]", file=sys.stderr)
        return 2
    remote = args[1]
    obj = args[2]
    flags = args[3:]
    json_mode = "--json" in flags
    tmp_dir = "/var/tmp/wolf"
    state_dir = "/var/lib/wolf"
    for i, flag in enumerate(flags):
        if flag == "--tmp-dir" and i + 1 < len(flags):
            tmp_dir = flags[i + 1]
        elif flag == "--state-dir" and i + 1 < len(flags):
            state_dir = flags[i + 1]
    rep = classify_archive(remote, obj, tmp_dir=tmp_dir, state_dir=state_dir)
    if json_mode:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(format_classification_report(rep))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))

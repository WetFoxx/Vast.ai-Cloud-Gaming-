"""tests/test_storage_classifier.py — тесты классификатора объектов облачного хранилища.

Проверяет классификацию в режиме аудита / предварительного просмотра (VIEW-ONLY):
1. Активная одиночная база с накопительной дельтой (ACTIVE).
2. Активная база частями (.parts) со своей дельтой частями (ACTIVE).
3. Доказанные кандидаты на очистку с конкретными основаниями, независимыми от возраста:
   - вытесненный одиночный архив при наличии активного оглавления .parts;
   - вытесненная дельта к старой базе (несовпадение хэша);
   - вытесненные части многокомпонентной старой дельты;
   - при этом неизвестные поколения базы без доказательств остаются PROTECTED.
4. Проверка реальных квитанций single и parts для базы и дельты, созданных непосредственно
   функциями format_receipt() и note_attempt() из настоящего встроенного wolf-parts.py:
   - имя архива с подчёркиванием;
   - имя архива с пробелом;
   - только attempt;
   - только pending.
5. Повреждённая квитанция в $S/pending или $T/attempt-* (блокировка очистки и защита кандидатов).
6. Неподтверждённые попытки со старым, равным, новым и отсутствующим mtime (все PROTECTED).
7. Объекты старого Wolf без новых метаданных (отсутствие session grant -> view-only).
8. Повреждённое оглавление частей, сбой листинга и посторонние файлы (Группа В / AMBIGUOUS).
9. Гарантия неизменности хранилища: снимок файлов и SHA-256 до и после классификации строго идентичен.
10. Чистое восстановление: распаковка активных данных даёт исходные пути и SHA-256.
11. Синхронизация исходного wolf_classifier.py и встроенной копии в wolf-setup.sh.
"""

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from wolf_classifier import (
    PARTS_HEAD,
    classify_archive,
    format_classification_report,
    parse_parts_index_text,
    parse_receipt,
)


def load_real_wolf_parts():
    """Извлекает и компилирует настоящий встроенный wolf-parts.py из wolf-setup.sh.
    Тестовые квитанции формируются строго его функциями format_receipt() и note_attempt()."""
    setup_path = Path(__file__).resolve().parent.parent / "wolf-setup.sh"
    setup_content = setup_path.read_text(encoding="utf-8")
    start_marker = "w /usr/local/bin/wolf-parts.py <<'PARTS'\n"
    end_marker = "\nPARTS\n"
    s_idx = setup_content.index(start_marker) + len(start_marker)
    e_idx = setup_content.index(end_marker, s_idx)
    parts_code = setup_content[s_idx:e_idx]
    ns = {}
    exec(compile(parts_code, "wolf-parts.py", "exec"), ns)
    return ns


# Загружаем модуль настоящего движка wolf-parts.py
_WOLF_PARTS = load_real_wolf_parts()
engine_format_receipt = _WOLF_PARTS["format_receipt"]
engine_note_attempt = _WOLF_PARTS["note_attempt"]


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dir_snapshot(path: Path) -> dict:
    """Снимок всех файлов в каталоге: {относительный_путь: (размер, sha256)}."""
    snap = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            rel = p.relative_to(path).as_posix()
            content = p.read_bytes()
            snap[rel] = (len(content), sha256_bytes(content))
    return snap


def make_tar_bytes(file_dict: dict) -> bytes:
    """Создаёт tar-архив в памяти из словаря {относительный_путь: содержимое_bytes}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in file_dict.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = 1700000000
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class TestStorageClassifier(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.storage_dir = self.td / "remote_storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.l_dir = self.td / "run_wolf"
        self.l_dir.mkdir(parents=True, exist_ok=True)
        self.t_dir = self.td / "var_tmp_wolf"
        self.t_dir.mkdir(parents=True, exist_ok=True)
        self.s_dir = self.td / "var_lib_wolf"
        self.s_dir.mkdir(parents=True, exist_ok=True)
        self.grant_file = self.l_dir / "cleanup_grant.json"

        # Настраиваем окружение настоящего wolf-parts.py для записи в тестовый tmp_dir
        _WOLF_PARTS["TMP"] = str(self.t_dir)
        os.environ["WOLF_TMP"] = str(self.t_dir)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _setup_active_grant(self, run_id="test_run_123"):
        self.grant_file.write_text(
            json.dumps({"status": "granted", "run_id": run_id, "iid": "1001", "folder_id": "fid_42"}),
            encoding="utf-8",
        )

    def _write_engine_pending(self, archive_name: str, target_obj: str, receipt: tuple):
        """Формирует $S/pending/<archive_name> из строки 'object <target_obj>'
        и реальной квитанции движка format_receipt(), строго как делает pack()."""
        receipt_str = engine_format_receipt(receipt)
        p_dir = self.s_dir / "pending"
        p_dir.mkdir(parents=True, exist_ok=True)
        path = p_dir / archive_name
        path.write_text(f"object {target_obj}\n{receipt_str}", encoding="utf-8")
        return path

    def test_embedded_classifier_matches_standalone(self):
        """Проверка совпадения исходного wolf_classifier.py и встроенного в wolf-setup.sh."""
        root_dir = Path(__file__).resolve().parent.parent
        setup_path = root_dir / "wolf-setup.sh"
        classifier_path = root_dir / "wolf_classifier.py"

        self.assertTrue(setup_path.is_file(), "wolf-setup.sh не найден")
        self.assertTrue(classifier_path.is_file(), "wolf_classifier.py не найден")

        setup_content = setup_path.read_text(encoding="utf-8")
        start_marker = "w /usr/local/lib/wolf_classifier.py 644 <<'CLASSIFIER'\n"
        end_marker = "\nCLASSIFIER\n"

        self.assertIn(start_marker, setup_content)
        self.assertIn(end_marker, setup_content)

        s_idx = setup_content.index(start_marker) + len(start_marker)
        e_idx = setup_content.index(end_marker)
        embedded_code = setup_content[s_idx:e_idx].strip()
        standalone_code = classifier_path.read_text(encoding="utf-8").strip()

        self.assertEqual(embedded_code, standalone_code, "Встроенный wolf_classifier.py в wolf-setup.sh расходится с оригиналом!")

    def test_active_single_base_and_delta(self):
        """Сценарий 1: Активная одиночная база с дельтой.
        - База sgame--TestA.tar.zst
        - Дельта sgame--TestA.tar.zst.<h_base[:8]>
        - Обе попадают в ACTIVE;
        - Хранилище до и после классификации неизменно;
        - Распаковка активных данных даёт точное совпадение файлов по SHA-256."""
        obj = "sgame--TestA.tar.zst"
        base_files = {"game.bin": b"BASE_DATA_V1_AAA" * 100, "config.cfg": b"cfg=1\n"}
        base_tar_content = make_tar_bytes(base_files)
        base_md5 = md5_bytes(base_tar_content)
        delta_hash = base_md5[:8]

        delta_files = {"delta.patch": b"PATCH_DELTA_CONTENT" * 50, "save.dat": b"SAVE_STATE_1\n"}
        delta_tar_content = make_tar_bytes(delta_files)

        (self.storage_dir / obj).write_bytes(base_tar_content)
        (self.storage_dir / f"{obj}.{delta_hash}").write_bytes(delta_tar_content)

        snap_before = dir_snapshot(self.storage_dir)

        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )

        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after, "Хранилище изменилось во время классификации!")

        self.assertEqual(report["summary"]["active_count"], 2)
        self.assertEqual(report["summary"]["candidate_count"], 0)
        self.assertEqual(report["summary"]["protected_count"], 0)
        self.assertEqual(report["summary"]["ambiguous_count"], 0)
        self.assertFalse(report["cleanup_allowed"])
        self.assertEqual(report["cleanup_block_reason"], "no_candidates_to_clean")

        by_cat = {f["name"]: f["category"] for f in report["files"]}
        self.assertEqual(by_cat[obj], "ACTIVE")
        self.assertEqual(by_cat[f"{obj}.{delta_hash}"], "ACTIVE")

        unpack_dir = self.td / "unpack"
        unpack_dir.mkdir()
        with tarfile.open(fileobj=io.BytesIO((self.storage_dir / obj).read_bytes())) as tf:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(unpack_dir, filter="data")
            else:
                tf.extractall(unpack_dir)
        self.assertEqual(sha256_bytes((unpack_dir / "game.bin").read_bytes()), sha256_bytes(base_files["game.bin"]))
        self.assertEqual(sha256_bytes((unpack_dir / "config.cfg").read_bytes()), sha256_bytes(base_files["config.cfg"]))

    def test_active_multipart_base_and_delta(self):
        """Сценарий 2: Активная база частями (.parts) со своей дельтой частями.
        - Оглавления и все перечисленные части входят в ACTIVE;
        - Хранилище неизменно;
        - Собранные части восстанавливаются чисто."""
        obj = "sgame--TestB.tar.zst"
        p1 = b"PART_1_BASE_DATA" * 500
        p2 = b"PART_2_BASE_DATA" * 500
        gen_base = "11112222"
        p1_name = f"{obj}.g{gen_base}-0000"
        p2_name = f"{obj}.g{gen_base}-0001"
        (self.storage_dir / p1_name).write_bytes(p1)
        (self.storage_dir / p2_name).write_bytes(p2)

        total_base = len(p1) + len(p2)
        index_base = f"{PARTS_HEAD}\n{total_base}\n{p1_name}\t{md5_bytes(p1)}\t{len(p1)}\n{p2_name}\t{md5_bytes(p2)}\t{len(p2)}\n"
        (self.storage_dir / f"{obj}.parts").write_text(index_base, encoding="utf-8")
        base_h = md5_bytes(index_base.encode("utf-8"))
        delta_hash = base_h[:8]

        # Дельта частями
        dp1 = b"DELTA_PART_DATA" * 200
        gen_delta = "33334444"
        dp1_name = f"{obj}.{delta_hash}.g{gen_delta}-0000"
        (self.storage_dir / dp1_name).write_bytes(dp1)
        index_delta = f"{PARTS_HEAD}\n{len(dp1)}\n{dp1_name}\t{md5_bytes(dp1)}\t{len(dp1)}\n"
        (self.storage_dir / f"{obj}.{delta_hash}.parts").write_text(index_delta, encoding="utf-8")

        snap_before = dir_snapshot(self.storage_dir)

        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )

        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

        self.assertEqual(report["summary"]["active_count"], 5)  # 2 оглавления + 3 части = 5 файлов
        self.assertEqual(report["summary"]["candidate_count"], 0)
        self.assertEqual(report["summary"]["protected_count"], 0)
        self.assertEqual(report["summary"]["ambiguous_count"], 0)

        assembled = (self.storage_dir / p1_name).read_bytes() + (self.storage_dir / p2_name).read_bytes()
        self.assertEqual(len(assembled), total_base)
        self.assertEqual(sha256_bytes(assembled), sha256_bytes(p1 + p2))

    def test_displaced_candidates_with_concrete_age_independent_grounds(self):
        """Сценарий 3: Доказанные кандидаты на очистку с основаниями, независимыми от возраста.
        - Вытесненный одиночный архив базы -> CANDIDATE (displaced_single_archive_superseded_by_active_parts_index);
        - Вытесненная старая дельта к чужой базе -> CANDIDATE (displaced_delta_superseded_by_newer_base);
        - Вытесненные части многокомпонентной старой дельты -> CANDIDATE (displaced_delta_parts_index/part...);
        - Неизвестное поколение базы gen=11110000 -> PROTECTED (независимо от старого/нового mtime!);
        - Хранилище неизменно."""
        obj = "sgame--TestC.tar.zst"

        # Активная база
        p_act = b"ACTIVE_PART_C" * 200
        gen_act = "22223333"
        p_act_name = f"{obj}.g{gen_act}-0000"
        (self.storage_dir / p_act_name).write_bytes(p_act)

        index_text = f"{PARTS_HEAD}\n{len(p_act)}\n{p_act_name}\t{md5_bytes(p_act)}\t{len(p_act)}\n"
        (self.storage_dir / f"{obj}.parts").write_text(index_text, encoding="utf-8")

        # 1. Структурно вытесненный одиночный архив
        (self.storage_dir / obj).write_bytes(b"OLD_SINGLE_BASE_BYTES" * 100)

        # 2. Структурно вытесненная дельта к чужой базе
        (self.storage_dir / f"{obj}.deadbeef").write_bytes(b"OLD_DELTA_BYTES" * 50)

        # 3. Структурно вытесненная многокомпонентная дельта к чужой базе
        old_delta_parts_name = f"{obj}.c0ffee00.g77778888-0000"
        (self.storage_dir / old_delta_parts_name).write_bytes(b"OLD_DELTA_PART" * 40)
        idx_old_delta = f"{PARTS_HEAD}\n{len(b'OLD_DELTA_PART' * 40)}\n{old_delta_parts_name}\t{md5_bytes(b'OLD_DELTA_PART' * 40)}\t{len(b'OLD_DELTA_PART' * 40)}\n"
        (self.storage_dir / f"{obj}.c0ffee00.parts").write_text(idx_old_delta, encoding="utf-8")

        # 4. Неподтверждённая часть прежнего поколения базы со старым mtime (1000.0)
        old_base_part = self.storage_dir / f"{obj}.g11110000-0000"
        old_base_part.write_bytes(b"UNCONFIRMED_BASE_PART" * 80)
        os.utime(old_base_part, (1000.0, 1000.0))

        snap_before = dir_snapshot(self.storage_dir)

        # Без разрешения сессии: VIEW-ONLY
        report_no_grant = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )
        self.assertEqual(report_no_grant["summary"]["active_count"], 2)
        self.assertEqual(report_no_grant["summary"]["candidate_count"], 4)
        self.assertEqual(report_no_grant["summary"]["protected_count"], 1)
        self.assertFalse(report_no_grant["cleanup_allowed"])
        self.assertEqual(report_no_grant["cleanup_block_reason"], "view_only_no_session_grant")

        by_name = {f["name"]: f for f in report_no_grant["files"]}
        self.assertEqual(by_name[obj]["category"], "CANDIDATE")
        self.assertEqual(by_name[obj]["reason"], "displaced_single_archive_superseded_by_active_parts_index")

        self.assertEqual(by_name[f"{obj}.deadbeef"]["category"], "CANDIDATE")
        self.assertEqual(by_name[f"{obj}.deadbeef"]["reason"], "displaced_delta_superseded_by_newer_base")

        self.assertEqual(by_name[f"{obj}.c0ffee00.parts"]["category"], "CANDIDATE")
        self.assertEqual(by_name[f"{obj}.c0ffee00.parts"]["reason"], "displaced_delta_parts_index_superseded_by_newer_base")

        self.assertEqual(by_name[old_delta_parts_name]["category"], "CANDIDATE")
        self.assertEqual(by_name[old_delta_parts_name]["reason"], "displaced_delta_part_superseded_by_newer_base")

        self.assertEqual(by_name[f"{obj}.g11110000-0000"]["category"], "PROTECTED")
        self.assertEqual(by_name[f"{obj}.g11110000-0000"]["reason"], "unconfirmed_generation_without_proven_displacement")

        # С сессионным разрешением
        self._setup_active_grant()
        report_with_grant = classify_archive(
            str(self.storage_dir),
            obj,
            grant_file=str(self.grant_file),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val="test_run_123",
            iid_val="1001",
            folder_id_val="fid_42",
        )
        self.assertTrue(report_with_grant["cleanup_allowed"])
        self.assertIsNone(report_with_grant["cleanup_block_reason"])

        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

    def test_real_receipt_name_with_underscore_attempts_and_pending(self):
        """Сценарий 4а: Настоящие квитанции движка для архива с подчёркиванием в имени.
        Проверяются:
        - имя с подчёркиванием (game_with_underscore.tar.zst);
        - single база через attempt (only attempt);
        - parts база через pending (only pending);
        - single дельта через pending (only pending);
        - parts дельта через attempt (only attempt).
        Прямое преобразование имени без replace('_', '/') исключает искажение имени."""
        base_name = "sgame--my_cool_game"
        obj = f"{base_name}.tar.zst"

        # Активная база частями
        p_act = b"ACT_DATA_UNDER" * 100
        gen_act = "11112222"
        p_act_name = f"{obj}.g{gen_act}-0000"
        (self.storage_dir / p_act_name).write_bytes(p_act)
        idx_act = f"{PARTS_HEAD}\n{len(p_act)}\n{p_act_name}\t{md5_bytes(p_act)}\t{len(p_act)}\n"
        (self.storage_dir / f"{obj}.parts").write_text(idx_act, encoding="utf-8")

        # 1. Single база: только attempt-* (настоящий note_attempt из wolf-parts.py)
        # Одиночный архив obj лежит рядом с .parts
        (self.storage_dir / obj).write_bytes(b"SINGLE_BASE_CONTENT")
        rec_single_base = ("single", md5_bytes(b"SINGLE_BASE_CONTENT"), len(b"SINGLE_BASE_CONTENT"))
        engine_note_attempt(obj, rec_single_base, "unknown")

        # 2. Parts база: только pending в $S/pending/sgame--my_cool_game (формат pack)
        gen_pend = "33334444"
        p_pend_name = f"{obj}.g{gen_pend}-0000"
        (self.storage_dir / p_pend_name).write_bytes(b"PENDING_PART_DATA")
        rec_parts_base = ("parts", gen_pend, md5_bytes(b"idx_p"), 100, [(p_pend_name, md5_bytes(b"PENDING_PART_DATA"), len(b"PENDING_PART_DATA"))], 1700000000.0)
        self._write_engine_pending(base_name, obj, rec_parts_base)

        # 3. Single дельта: только pending (к другой базе .c0ffee00) через $S/pending/<base_tar>
        delta_single_obj = f"{obj}.c0ffee00"
        (self.storage_dir / delta_single_obj).write_bytes(b"SINGLE_DELTA_CONTENT")
        rec_single_delta = ("single", md5_bytes(b"SINGLE_DELTA_CONTENT"), len(b"SINGLE_DELTA_CONTENT"))
        self._write_engine_pending(obj, delta_single_obj, rec_single_delta)

        # 4. Parts дельта: только attempt-* (к другой базе .deadbeef)
        delta_parts_obj = f"{obj}.deadbeef"
        gen_delta_att = "55556666"
        p_d_name = f"{delta_parts_obj}.g{gen_delta_att}-0000"
        (self.storage_dir / p_d_name).write_bytes(b"DELTA_ATTEMPT_PART_DATA")
        rec_parts_delta = ("parts", gen_delta_att, md5_bytes(b"idx_d"), 100, [(p_d_name, md5_bytes(b"DELTA_ATTEMPT_PART_DATA"), len(b"DELTA_ATTEMPT_PART_DATA"))], 1700000000.0)
        engine_note_attempt(delta_parts_obj, rec_parts_delta, "unknown")

        snap_before = dir_snapshot(self.storage_dir)
        self._setup_active_grant()
        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )
        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

        # Ошибок парсинга квитанций быть не должно, все объекты защищены квитанциями
        self.assertFalse(report["cleanup_allowed"])
        self.assertEqual(report["cleanup_block_reason"], "no_candidates_to_clean")
        self.assertEqual(report["summary"]["candidate_count"], 0)

        by_name = {f["name"]: f for f in report["files"]}
        # Проверяем точные имена с подчёркиванием:
        # 1. Single база из attempt -> PROTECTED
        self.assertEqual(by_name[obj]["category"], "PROTECTED")
        self.assertEqual(by_name[obj]["reason"], "pending_attempt_single_archive_matching_receipt")

        # 2. Parts база из pending -> PROTECTED
        self.assertEqual(by_name[p_pend_name]["category"], "PROTECTED")
        self.assertEqual(by_name[p_pend_name]["reason"], "pending_attempt_part_matching_receipt")

        # 3. Single дельта из pending -> PROTECTED
        self.assertEqual(by_name[delta_single_obj]["category"], "PROTECTED")
        self.assertEqual(by_name[delta_single_obj]["reason"], "pending_attempt_or_staged_delta_protected")

        # 4. Parts дельта из attempt -> PROTECTED
        self.assertEqual(by_name[p_d_name]["category"], "PROTECTED")
        self.assertEqual(by_name[p_d_name]["reason"], "pending_attempt_part_matching_receipt")

    def test_real_receipt_name_with_space_attempts_and_pending(self):
        """Сценарий 4б: Настоящие квитанции движка для архива с пробелом в имени.
        Проверяются:
        - имя с пробелом (sgame--my game space.tar.zst);
        - single дельта через attempt (only attempt);
        - parts дельта через pending (only pending);
        - single база через pending (only pending);
        - parts база через attempt (only attempt).
        Пробелы в именах частей и файлов корректно поддерживаются."""
        base_name = "sgame--my game space"
        obj = f"{base_name}.tar.zst"

        # Активная база частями
        p_act = b"ACT_DATA_SPACE" * 100
        gen_act = "22223333"
        p_act_name = f"{obj}.g{gen_act}-0000"
        (self.storage_dir / p_act_name).write_bytes(p_act)
        idx_act = f"{PARTS_HEAD}\n{len(p_act)}\n{p_act_name}\t{md5_bytes(p_act)}\t{len(p_act)}\n"
        (self.storage_dir / f"{obj}.parts").write_text(idx_act, encoding="utf-8")

        # 1. Parts база: только attempt-* (настоящий note_attempt)
        gen_att_b = "77778888"
        p_att_b_name = f"{obj}.g{gen_att_b}-0000"
        (self.storage_dir / p_att_b_name).write_bytes(b"BASE_ATT_SPACE_PART")
        rec_parts_b = ("parts", gen_att_b, md5_bytes(b"idx_ab"), 100, [(p_att_b_name, md5_bytes(b"BASE_ATT_SPACE_PART"), len(b"BASE_ATT_SPACE_PART"))], 1700000000.0)
        engine_note_attempt(obj, rec_parts_b, "unknown")

        # 2. Single дельта к чужой базе (.11223344): только attempt-*
        delta_single_obj = f"{obj}.11223344"
        (self.storage_dir / delta_single_obj).write_bytes(b"DELTA_SINGLE_SPACE")
        rec_single_d = ("single", md5_bytes(b"DELTA_SINGLE_SPACE"), len(b"DELTA_SINGLE_SPACE"))
        engine_note_attempt(delta_single_obj, rec_single_d, "unknown")

        # 3. Parts дельта к чужой базе (.55667788): только pending в $S/pending
        delta_parts_obj = f"{obj}.55667788"
        gen_pend_d = "99990000"
        p_pend_d_name = f"{delta_parts_obj}.g{gen_pend_d}-0000"
        (self.storage_dir / p_pend_d_name).write_bytes(b"DELTA_PEND_SPACE_PART")
        rec_parts_d = ("parts", gen_pend_d, md5_bytes(b"idx_pd"), 100, [(p_pend_d_name, md5_bytes(b"DELTA_PEND_SPACE_PART"), len(b"DELTA_PEND_SPACE_PART"))], 1700000000.0)
        self._write_engine_pending(base_name, delta_parts_obj, rec_parts_d)

        # 4. Single база: только pending в $S/pending (одиночный файл рядом с .parts)
        (self.storage_dir / obj).write_bytes(b"SINGLE_BASE_SPACE_BYTES")
        rec_single_b = ("single", md5_bytes(b"SINGLE_BASE_SPACE_BYTES"), len(b"SINGLE_BASE_SPACE_BYTES"))
        self._write_engine_pending(obj, obj, rec_single_b)

        snap_before = dir_snapshot(self.storage_dir)
        self._setup_active_grant()
        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )
        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

        # Ошибок парсинга квитанций нет, кандидатов на очистку нет (все защищены)
        self.assertFalse(report["cleanup_allowed"])
        self.assertEqual(report["cleanup_block_reason"], "no_candidates_to_clean")
        self.assertEqual(report["summary"]["candidate_count"], 0)

        by_name = {f["name"]: f for f in report["files"]}
        # Проверяем защиту объектов с пробелами:
        # 1. Parts база из attempt -> PROTECTED
        self.assertEqual(by_name[p_att_b_name]["category"], "PROTECTED")
        self.assertEqual(by_name[p_att_b_name]["reason"], "pending_attempt_part_matching_receipt")

        # 2. Single дельта из attempt -> PROTECTED
        self.assertEqual(by_name[delta_single_obj]["category"], "PROTECTED")
        self.assertEqual(by_name[delta_single_obj]["reason"], "pending_attempt_or_staged_delta_protected")

        # 3. Parts дельта из pending -> PROTECTED
        self.assertEqual(by_name[p_pend_d_name]["category"], "PROTECTED")
        self.assertEqual(by_name[p_pend_d_name]["reason"], "pending_attempt_part_matching_receipt")

        # 4. Single база из pending -> PROTECTED
        self.assertEqual(by_name[obj]["category"], "PROTECTED")
        self.assertEqual(by_name[obj]["reason"], "pending_attempt_single_archive_matching_receipt")

    def test_corrupted_receipt_blocks_cleanup_and_protects_candidates(self):
        """Сценарий 5: Повреждённая квитанция в $S/pending или $T/attempt-*.
        Ошибка чтения или синтаксиса НЕ игнорируется:
        - cleanup_allowed=False с указанием на повреждённую квитанцию;
        - потенциальные кандидаты переводятся в PROTECTED;
        - хранилище неизменно."""
        obj = "sgame--TestCorruptReceipt.tar.zst"
        p_act = b"ACT_DATA" * 100
        gen_act = "44444444"
        (self.storage_dir / f"{obj}.g{gen_act}-0000").write_bytes(p_act)
        idx_act = f"{PARTS_HEAD}\n{len(p_act)}\n{obj}.g{gen_act}-0000\t{md5_bytes(p_act)}\t{len(p_act)}\n"
        (self.storage_dir / f"{obj}.parts").write_text(idx_act, encoding="utf-8")

        # Потенциальный кандидат (одиночная база)
        (self.storage_dir / obj).write_bytes(b"POTENTIAL_CANDIDATE_SINGLE")

        # Повреждённый файл квитанции в $S/pending
        pend_file = self.s_dir / "pending" / "sgame--TestCorruptReceipt"
        pend_file.parent.mkdir(parents=True, exist_ok=True)
        pend_file.write_text("object sgame--TestCorruptReceipt.tar.zst\nBROKEN_GARBAGE_RECEIPT\n", encoding="utf-8")

        snap_before = dir_snapshot(self.storage_dir)
        self._setup_active_grant()
        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )
        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

        self.assertFalse(report["cleanup_allowed"], "Повреждённая квитанция обязана блокировать очистку!")
        self.assertIn("corrupted_receipt_or_pending_attempt", report["cleanup_block_reason"])

        by_name = {f["name"]: f for f in report["files"]}
        self.assertEqual(by_name[obj]["category"], "PROTECTED")
        self.assertEqual(by_name[obj]["reason"], "corrupted_receipt_blocks_cleanup_candidate_protection")

    def test_unconfirmed_attempts_all_mtime_variants(self):
        """Сценарий 6: Старое, равное, новое и отсутствующее mtime.
        Во всех вариантах неподтверждённая попытка должна оставаться строго PROTECTED."""
        obj = "sgame--TestMtimes.tar.zst"
        now = 1700005000.0

        p_act = b"ACT_DATA" * 100
        gen_act = "12121212"
        p_act_file = self.storage_dir / f"{obj}.g{gen_act}-0000"
        p_act_file.write_bytes(p_act)
        os.utime(p_act_file, (now, now))

        idx_act = f"{PARTS_HEAD}\n{len(p_act)}\n{obj}.g{gen_act}-0000\t{md5_bytes(p_act)}\t{len(p_act)}\n"
        idx_file = self.storage_dir / f"{obj}.parts"
        idx_file.write_text(idx_act, encoding="utf-8")
        os.utime(idx_file, (now, now))

        # 1. Попытка со старым mtime (< now)
        p_old = self.storage_dir / f"{obj}.g00000001-0000"
        p_old.write_bytes(b"OLD_MTIME_ATTEMPT")
        os.utime(p_old, (1000.0, 1000.0))

        # 2. Попытка с равным mtime (== now)
        p_eq = self.storage_dir / f"{obj}.g00000002-0000"
        p_eq.write_bytes(b"EQUAL_MTIME_ATTEMPT")
        os.utime(p_eq, (now, now))

        # 3. Попытка с новым mtime (> now)
        p_new = self.storage_dir / f"{obj}.g00000003-0000"
        p_new.write_bytes(b"NEW_MTIME_ATTEMPT")
        os.utime(p_new, (now + 1000.0, now + 1000.0))

        # 4. Попытка с отсутствующим / нулевым mtime (0.0)
        p_zero = self.storage_dir / f"{obj}.g00000004-0000"
        p_zero.write_bytes(b"ZERO_MTIME_ATTEMPT")
        os.utime(p_zero, (0.0, 0.0))

        snap_before = dir_snapshot(self.storage_dir)
        self._setup_active_grant()
        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )
        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

        by_name = {f["name"]: f for f in report["files"]}
        for part_name in (p_old.name, p_eq.name, p_new.name, p_zero.name):
            self.assertEqual(by_name[part_name]["category"], "PROTECTED", f"{part_name} не был защищён!")
            self.assertEqual(by_name[part_name]["reason"], "unconfirmed_generation_without_proven_displacement")

        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_legacy_wolf_objects_without_new_metadata(self):
        """Сценарий 7: Объекты старого Wolf без новых метаданных.
        - В хранилище лежат старые архивы v4.2;
        - /run/wolf не содержит grant/run_id;
        - Активные данные сохраняются, очистка кандидатов заблокирована (VIEW-ONLY)."""
        obj = "sgame--TestE.tar.zst"
        (self.storage_dir / obj).write_bytes(b"LEGACY_WOLF_BASE_V4" * 100)

        h = md5_bytes((self.storage_dir / obj).read_bytes())
        (self.storage_dir / f"{obj}.{h[:8]}").write_bytes(b"LEGACY_WOLF_DELTA_V4" * 50)

        snap_before = dir_snapshot(self.storage_dir)

        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )

        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

        self.assertEqual(report["summary"]["active_count"], 2)
        self.assertEqual(report["summary"]["candidate_count"], 0)
        self.assertFalse(report["cleanup_allowed"])

    def test_corrupted_index_and_unknown_files(self):
        """Сценарий 8: Повреждённое оглавление и неизвестные файлы.
        - Повреждённое оглавление .parts классифицируется как AMBIGUOUS;
        - Посторонний файл .tmp классифицируется как AMBIGUOUS;
        - Очистка объекта полностью блокируется (cleanup_allowed=False);
        - Хранилище неизменно."""
        obj = "sgame--TestF.tar.zst"
        (self.storage_dir / f"{obj}.parts").write_text("CORRUPTED_GARBAGE_INDEX_HEADER\n123\n", encoding="utf-8")
        (self.storage_dir / f"{obj}.g11112222-0000").write_bytes(b"PART_F" * 50)
        (self.storage_dir / f"{obj}.unknown_file.tmp").write_bytes(b"UNKNOWN_TMP_DATA")

        snap_before = dir_snapshot(self.storage_dir)

        self._setup_active_grant()
        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )

        snap_after = dir_snapshot(self.storage_dir)
        self.assertEqual(snap_before, snap_after)

        self.assertGreater(report["summary"]["ambiguous_count"], 0)
        self.assertFalse(report["cleanup_allowed"])
        self.assertEqual(report["cleanup_block_reason"], "has_ambiguous_or_corrupted_objects")

        by_name = {f["name"]: f for f in report["files"]}
        self.assertEqual(by_name[f"{obj}.parts"]["category"], "AMBIGUOUS")
        self.assertEqual(by_name[f"{obj}.unknown_file.tmp"]["category"], "AMBIGUOUS")

    def test_format_report_no_secrets(self):
        """Сценарий 9: Форматированный отчёт содержит требуемые поля и не включает секреты."""
        obj = "sgame--SecretTest.tar.zst"
        (self.storage_dir / obj).write_bytes(b"DATA" * 50)

        report = classify_archive(
            str(self.storage_dir), obj, grant_file=str(self.grant_file), tmp_dir=str(self.t_dir), state_dir=str(self.s_dir)
        )
        text = format_classification_report(report)

        self.assertIn("ОТЧЁТ КЛАССИФИКАЦИИ ОБЛАЧНОГО ОБЪЕКТА: sgame--SecretTest.tar.zst", text)
        self.assertIn("Статус очистки:", text)
        self.assertIn("[АКТИВНЫЕ]", text)
        self.assertIn("[ЗАЩИЩЁННЫЕ]", text)
        self.assertIn("[КАНДИДАТЫ]", text)
        self.assertIn("ИТОГ: удаление или перемещение файлов НЕ производится", text)

        for secret_kw in ("password", "secret", "token", "auth_key", "Bearer"):
            self.assertNotIn(secret_kw, text)

    def test_cli_diagnostic_command_stdout_and_json(self):
        """Сценарий 10: Явная диагностическая команда wolf_classifier.py (текстовый и JSON режимы)."""
        import subprocess, sys
        obj = "sgame--CliTest.tar.zst"
        (self.storage_dir / obj).write_bytes(b"CLI_TEST_DATA" * 50)

        res_txt = subprocess.run(
            [sys.executable, "wolf_classifier.py", str(self.storage_dir), obj],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(res_txt.returncode, 0)
        self.assertIn("ОТЧЁТ КЛАССИФИКАЦИИ ОБЛАЧНОГО ОБЪЕКТА: sgame--CliTest.tar.zst", res_txt.stdout)
        self.assertIn("[АКТИВНЫЕ]", res_txt.stdout)

        res_json = subprocess.run(
            [sys.executable, "wolf_classifier.py", str(self.storage_dir), obj, "--json"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(res_json.returncode, 0)
        parsed = json.loads(res_json.stdout)
        self.assertEqual(parsed["object"], "sgame--CliTest.tar.zst")
        self.assertEqual(parsed["summary"]["active_count"], 1)
        self.assertFalse(parsed["cleanup_allowed"])


if __name__ == "__main__":
    unittest.main()

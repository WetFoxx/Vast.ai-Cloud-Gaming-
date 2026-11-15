#!/usr/bin/env python3
"""tests/test_storage_cleaner_drive.py — Интеграционные проверки безопасной очистки на Google Drive.

Работает ИСКЛЮЧИТЕЛЬНО на специально созданной изолированной папке:
`vastgame-cleanup-test-<уникальный идентификатор>`.

Никакие пользовательские архивы и рабочая папка vastai-cloud-games не затрагиваются.
Предел суммарной загрузки: 100 МиБ.
Реализует полную матрицу сценариев А, Б, В, Г, Д, Е.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
import uuid
from pathlib import Path

import requests

import keystore

import drive
from wolf_classifier import classify_archive, _get_remote_listing, parse_parts_index_text
from wolf_cleaner import execute_stand_cleanup

# Поиск бинарников
RCLONE_BIN = drive.paths.need("rclone")
ZSTD_BIN = next(
    (p for p in ("/opt/homebrew/bin/zstd", "/usr/bin/zstd", "/usr/local/bin/zstd") if os.path.exists(p)),
    "zstd",
)


def load_real_wolf_parts():
    """Извлекает и компилирует настоящий встроенный wolf-parts.py из wolf-setup.sh."""
    setup_path = Path(__file__).resolve().parent.parent / "wolf-setup.sh"
    setup_content = setup_path.read_text(encoding="utf-8")
    start_marker = "w /usr/local/bin/wolf-parts.py <<'PARTS'\n"
    end_marker = "\nPARTS\n"
    s_idx = setup_content.index(start_marker) + len(start_marker)
    e_idx = setup_content.index(end_marker, s_idx)
    parts_code = setup_content[s_idx:e_idx]
    ns = {}
    exec(compile(parts_code, "wolf-parts.py", "exec"), ns)
    return ns, parts_code


def load_real_wolf_script() -> str:
    """Извлекает исходный текст настоящего скрипта /usr/local/bin/wolf из wolf-setup.sh."""
    setup_path = Path(__file__).resolve().parent.parent / "wolf-setup.sh"
    setup_content = setup_path.read_text(encoding="utf-8")
    match = re.search(r"^w /usr/local/bin/wolf <<'WOLF'\n(.*?)^WOLF$", setup_content, re.S | re.M)
    assert match, "Не удалось найти w /usr/local/bin/wolf в wolf-setup.sh"
    return match.group(1)


WOLF_SCRIPT_CODE = load_real_wolf_script()


def wolf_func(name: str) -> str:
    """Извлекает определение функции из настоящего скрипта wolf."""
    found = (re.search(rf"^{name}\(\) *\{{[^\n]*\}}$", WOLF_SCRIPT_CODE, re.M)
             or re.search(rf"^{name}\(\) *\{{.*?\n\}}$", WOLF_SCRIPT_CODE, re.S | re.M))
    assert found, f"Функция {name} не найдена в настоящем скрипте wolf"
    return found.group(0)


_WOLF_PARTS, WOLF_PARTS_CODE = load_real_wolf_parts()


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_dir_tree_hashes(target_dir: Path) -> dict:
    """Возвращает словарь {относительный_путь: sha256_hex} для всех обычных файлов в каталоге."""
    res = {}
    for p in sorted(target_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(target_dir).as_posix()
            res[rel] = sha256_bytes(p.read_bytes())
    return res


def make_tar_zst(file_dict: dict) -> bytes:
    """Создаёт упакованный zstd tar-архив в памяти."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in file_dict.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = 1700000000
            tf.addfile(ti, io.BytesIO(data))
    tar_b = buf.getvalue()
    p = subprocess.Popen([ZSTD_BIN, "-q", "-T0", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(input=tar_b)
    if p.returncode != 0:
        raise RuntimeError(f"zstd compression failed: {err.decode('utf-8', 'replace')}")
    return out


def compress_zstd(data: bytes) -> bytes:
    """Сжимает байты через утилиту zstd."""
    p = subprocess.Popen([ZSTD_BIN, "-q", "-T0", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(input=data)
    if p.returncode != 0:
        raise RuntimeError(f"zstd compression failed: {err.decode('utf-8', 'replace')}")
    return out


def make_incompressible_payload(seed_str: str, size: int) -> bytes:
    """Генерирует воспроизводимые плохо сжимаемые синтетические данные фиксированного размера."""
    h = hashlib.sha256(seed_str.encode("utf-8"))
    chunks = []
    total = 0
    i = 0
    while total < size:
        h.update(str(i).encode("utf-8"))
        digest = h.digest()
        needed = min(len(digest), size - total)
        chunks.append(digest[:needed])
        total += needed
        i += 1
    return b"".join(chunks)


class DriveTestAdapter:
    """Тестовый адаптер для управляемой работы с Google Drive API и моделирования ошибок."""

    def __init__(self, folder_id: str, access_token: str, rclone_cmd: str, remote_name: str):
        self.folder_id = folder_id
        self.access_token = access_token
        self.rclone_cmd = rclone_cmd
        self.remote_name = remote_name
        self.mock_network_error = False
        self.mock_network_error_op = None

    def delete_candidate(self, fname: str, reg_entry: dict) -> tuple[bool, str | None]:
        if self.mock_network_error and (self.mock_network_error_op is None or self.mock_network_error_op == "delete"):
            return False, "network_connection_timeout (simulated in test adapter)"
        file_id = reg_entry.get("id") if isinstance(reg_entry, dict) else None
        if not file_id or not str(file_id).strip():
            return False, f"refusing delete: candidate {fname} has no registered file ID (name fallback forbidden)"
        if not self.access_token:
            return False, "missing_access_token_for_drive_api"
        try:
            res = requests.delete(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=30,
            )
            if res.status_code in (200, 204):
                return True, None
            return False, f"Drive API delete status: {res.status_code} ({res.text})"
        except Exception as e:
            return False, f"Drive API delete error: {e}"

    def verify_deleted(self, fname: str, reg_entry: dict) -> bool:
        if self.mock_network_error and (self.mock_network_error_op is None or self.mock_network_error_op == "verify"):
            return False
        file_id = reg_entry.get("id") if isinstance(reg_entry, dict) else None
        if not file_id or not str(file_id).strip():
            return False
        if not self.access_token:
            return False
        try:
            res = requests.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"fields": "id, trashed"},
                timeout=30,
            )
            if res.status_code == 404:
                return True
            if res.status_code == 200 and res.json().get("trashed") is True:
                return True
            return False
        except Exception:
            return False


class DriveStandManager:
    """Управляет жизненным циклом изолированной папки на Google Drive и учётом объектов."""

    MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # Лимит 100 МиБ

    def __init__(
        self,
        run_id: str | None = None,
        parent_folder_id: str | None = None,
        local_mode: bool = False,
        local_root_dir: Path | None = None,
    ):
        self.run_id = run_id or uuid.uuid4().hex[:16]
        self.parent_folder_id = parent_folder_id
        self.local_mode = local_mode

        # Каталог журнала и реестра
        if local_root_dir:
            self.local_root = Path(local_root_dir)
        else:
            self.local_root = Path(__file__).resolve().parent.parent / "artifacts" / f"drive_cleanup_stand_{self.run_id}"
        self.local_root.mkdir(parents=True, exist_ok=True)
        self.reports_dir = self.local_root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir = self.local_root / "evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.source_dir = self.local_root / "source_files"
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.local_root / "manifest.json"
        self.registry_path = self.local_root / "registry.json"

        self.registry = {}  # {name: {"id": file_id, "size": size, "sha256": sha256, ...}}
        self.deleted_bytes_candidates = 0
        self.cumulative_admitted_bytes = 0

        if local_mode:
            self.access_token = "mock_access_token_local"
            self.account_email = "local_test@example.com"
            self.folder_id = "mock_local_folder_id"
            self.folder_name = "vastgame-cleanup-test-local"
            self.web_view_link = "https://drive.google.com/drive/folders/mock_local_folder_id"
            self.remote_name = "vgtest:"
            self.rclone_env = os.environ.copy()
            self.adapter = DriveTestAdapter(
                folder_id=self.folder_id,
                access_token=self.access_token,
                rclone_cmd=RCLONE_BIN,
                remote_name=self.remote_name,
            )
            self._save_manifest()
            return

        keystore._in_tests = lambda: False
        self.access_token = drive._access_token()
        if not self.access_token:
            raise RuntimeError("Не удалось получить Google Drive access_token!")

        self.ident = drive.storage_identity()
        if not self.ident:
            raise RuntimeError("Не удалось определить идентичность хранилища Google Drive!")
        self.account_email = self.ident["account_email"]

        # Создание изолированной тестовой папки (в корне или внутри разрешённой папки)
        self.unique_id = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        if self.parent_folder_id:
            if self.parent_folder_id == "1vXf2vBiywk0SVf0bFPJTWfcg1Er9pemy":
                raise RuntimeError("КРИТИЧЕСКАЯ ОШИБКА: Запрещено использовать рабочую папку vastai-cloud-games!")
            self.folder_name = f"vastgame-cleanup-substand-{self.unique_id}"
            body = {
                "name": self.folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [self.parent_folder_id],
            }
        else:
            self.folder_name = f"vastgame-cleanup-test-{self.unique_id}"
            body = {
                "name": self.folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }

        create_res = requests.post(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {self.access_token}"},
            json=body,
            params={"fields": "id, name, webViewLink"},
            timeout=30,
        )
        if create_res.status_code != 200:
            raise RuntimeError(f"Ошибка создания тестовой папки Drive: {create_res.status_code} ({create_res.text})")

        f_data = create_res.json()
        self.folder_id = f_data["id"]
        self.web_view_link = f_data.get("webViewLink") or f"https://drive.google.com/drive/folders/{self.folder_id}"

        # Защита от случайного совпадения с рабочей папкой
        if self.folder_id == "1vXf2vBiywk0SVf0bFPJTWfcg1Er9pemy" or self.folder_name == "vastai-cloud-games":
            raise RuntimeError("КРИТИЧЕСКАЯ ОШИБКА: ID тестовой папки совпал с рабочей папкой!")

        # Настройка изолированного окружения rclone
        self.remote_name = "vgtest:"
        self.rclone_env = os.environ.copy()
        self.rclone_conf = self.local_root / "rclone.conf"
        self.rclone_env["RCLONE_CONFIG"] = str(self.rclone_conf)
        p = "RCLONE_CONFIG_VGTEST_"
        cid, sec = drive.client()
        vg_vars = {
            p + "TYPE": "drive",
            p + "SCOPE": drive.SCOPE,
            p + "CLIENT_ID": cid or "",
            p + "CLIENT_SECRET": sec or "",
            p + "TOKEN": json.dumps({
                "access_token": "x",
                "token_type": "Bearer",
                "refresh_token": drive.token(),
                "expiry": "2000-01-01T00:00:00Z",
            }),
            p + "ROOT_FOLDER_ID": self.folder_id,
        }
        self.rclone_env.update(vg_vars)
        os.environ.update(vg_vars)

        self.adapter = DriveTestAdapter(
            folder_id=self.folder_id,
            access_token=self.access_token,
            rclone_cmd=RCLONE_BIN,
            remote_name=self.remote_name,
        )

        self._save_manifest()

    @property
    def registered_objects_size_sum(self) -> int:
        """Сумма размеров содержимого всех зарегистрированных в реестре объектов стенда."""
        return sum(v.get("size", 0) for v in self.registry.values())

    @property
    def remaining_objects_size_sum(self) -> int:
        """Сумма размеров объектов, остающихся в хранилище (за вычетом удалённых кандидатов)."""
        return self.registered_objects_size_sum - self.deleted_bytes_candidates

    def _save_manifest(self):
        manifest_data = {
            "run_id": self.run_id,
            "folder_name": self.folder_name,
            "folder_id": self.folder_id,
            "parent_folder_id": self.parent_folder_id,
            "account_email": self.account_email,
            "web_view_link": self.web_view_link,
            "cumulative_admitted_bytes": self.cumulative_admitted_bytes,
            "registered_objects_size_sum": self.registered_objects_size_sum,
            "deleted_bytes_candidates": self.deleted_bytes_candidates,
            "remaining_objects_size_sum": self.remaining_objects_size_sum,
            "max_upload_bytes": self.MAX_UPLOAD_BYTES,
            "network_uploaded_bytes": None,
            "network_measurement_status": "unmeasured (сетевой трафик сокетов, HTTP-заголовки и повторы передачи не измерялись; учитываются размеры содержимого объектов)",
            "created_at": time.time(),
        }
        self.manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.registry_path.write_text(json.dumps(self.registry, indent=2, ensure_ascii=False), encoding="utf-8")

    def check_and_reserve_budget(self, incoming_bytes: int | None):
        """Проверяет и резервирует безопасный предел размера содержимого ДО передачи данных.
        Если размер передаваемых данных неизвестен или превышает доступный лимит,
        передача отклоняется до запуска сетевой команды.
        Повторные выгрузки и последовательные операции расходуют общий бюджет.
        """
        if incoming_bytes is None or incoming_bytes < 0:
            raise RuntimeError(
                f"Отказ до передачи: размер передаваемых данных не определён ({incoming_bytes}), "
                f"лимит хранилища стенда не может быть гарантирован."
            )
        if self.cumulative_admitted_bytes + incoming_bytes > self.MAX_UPLOAD_BYTES:
            raise RuntimeError(
                f"Отказ до передачи: превышение бюджета хранилища стенда "
                f"({self.cumulative_admitted_bytes} + {incoming_bytes} > {self.MAX_UPLOAD_BYTES} байт)."
            )
        self.cumulative_admitted_bytes += incoming_bytes
        self._save_manifest()

    def check_budget_before_transfer(self, incoming_bytes: int | None):
        """Псевдоним для проверки и резервирования бюджета ДО передачи."""
        self.check_and_reserve_budget(incoming_bytes)

    @staticmethod
    def estimate_archive_upload_bound(
        home_dir: Path | str | None,
        archive_name: str | None,
        part_bytes: int | None = None,
    ) -> int | None:
        """Определяет строго консервативную верхнюю границу размера содержимого создаваемых объектов ДО запуска упаковки.

        Учитывает:
        - точное выравнивание каждого файла на границу блока tar (512 байт);
        - заголовки tar (512 байт на каждый файл и каждый каталог);
        - расширенные заголовки GNU LongLink (512 байт заголовок + выровненный блок пути) для путей длиннее 100 байт;
        - концевые нулевые блоки tar (1024 байта);
        - предельные накладные расходы zstd для несжимаемых данных (RFC 8878 / compressBound: +1% + 1024 байта);
        - оглавление частей (.parts / .parts.new): заголовок + строка на каждую часть с учётом минимального размера части.

        Если путь не существует, недоступен для чтения, содержит недопустимые симлинки наружу или имя архива некорректно,
        возвращает None (размер неопределим -> отказ до запуска).
        """
        if not home_dir or not archive_name or not isinstance(archive_name, str):
            return None

        h_path = Path(home_dir).resolve()
        if not h_path.exists():
            return None

        # Определение целевого каталога по префиксу имени архива (согласно spec() в wolf-setup.sh)
        if archive_name.startswith("sgame--"):
            game_id = archive_name[len("sgame--"):]
            target_dir = h_path / "sa" / "steamapps" / "common" / game_id
        elif archive_name.startswith("game--"):
            game_id = archive_name[len("game--"):]
            target_dir = h_path / "sa" / "steamapps" / "common" / game_id
        elif archive_name.startswith("pfx--"):
            game_id = archive_name[len("pfx--"):]
            target_dir = h_path / "sa" / "steamapps" / "compatdata" / game_id
        else:
            target_dir = h_path / archive_name

        if not target_dir.exists() or not target_dir.is_dir():
            return None

        try:
            tar_raw_bytes = 0
            file_count = 0

            # Обход каталога, аналогичный find в Wolf (cwd = $B = h_path)
            for root, dirs, files in os.walk(target_dir):
                root_path = Path(root)
                # Учёт каталогов в tar
                rel_dir = root_path.relative_to(h_path).as_posix()
                if rel_dir != ".":
                    tar_raw_bytes += 512  # tar directory header
                    dir_bytes_len = len(rel_dir.encode("utf-8"))
                    if dir_bytes_len > 100:
                        # GNU LongLink header (512) + padded name block
                        tar_raw_bytes += 512 + ((dir_bytes_len + 1 + 511) // 512) * 512

                for fname in files:
                    fpath = root_path / fname
                    if fpath.is_symlink():
                        resolved = fpath.resolve()
                        if not resolved.is_relative_to(h_path):
                            return None
                    st = fpath.stat()
                    file_size = st.st_size
                    rel_file = fpath.relative_to(h_path).as_posix()
                    file_bytes_len = len(rel_file.encode("utf-8"))

                    # Tar header: 512 байт
                    tar_raw_bytes += 512
                    # GNU LongLink header + data для длинных имён (> 100 байт)
                    if file_bytes_len > 100:
                        tar_raw_bytes += 512 + ((file_bytes_len + 1 + 511) // 512) * 512
                    # Данные файла с выравниванием на 512 байт
                    tar_raw_bytes += ((file_size + 511) // 512) * 512
                    file_count += 1

            if file_count == 0:
                return None

            # Концевые нулевые блоки tar: 1024 байта
            tar_raw_bytes += 1024

            # Предельные накладные расходы сжатия zstd (худший случай — несжимаемые данные):
            # +1% от несжатого объёма + 1024 байта на frame header, block headers и контрольные суммы
            max_compressed_bytes = int(tar_raw_bytes * 1.01) + 1024

            # Определение максимального числа частей и объёма оглавления (.parts / .parts.new)
            # Если part_bytes не задан, используем безопасную нижнюю оценку 1000 байт для расчёта максимального числа строк
            chunk_size = part_bytes if (part_bytes and part_bytes > 0) else 1000
            max_parts = max(1, (max_compressed_bytes + chunk_size - 1) // chunk_size)

            # Строка части в оглавлении: <archive_name>.<base_hash>.<gen>-<idx:04d>\t<md5>\t<size>\n
            line_size_bound = len(archive_name.encode("utf-8")) + 128
            index_file_bound = 128 + max_parts * line_size_bound

            # Суммарная консервативная граница: сжатые части + оглавление
            return max_compressed_bytes + index_file_bound
        except Exception:
            return None

    def record_candidate_deletion(self, size_bytes: int):
        """Учитывает размер подтверждённо удалённого кандидата."""
        self.deleted_bytes_candidates += size_bytes
        self._save_manifest()

    def save_evidence(self, fname: str, data: dict | list | str) -> Path:
        """Сохраняет артефакт доказательства сценария в каталоге evidence."""
        p = self.evidence_dir / fname
        if isinstance(data, (dict, list)):
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            p.write_text(str(data), encoding="utf-8")
        return p

    def register_created_object(self, name: str, data_bytes: bytes, file_id: str | None = None):
        """Регистрирует созданный объект в локальном журнале стенда с обязательной привязкой к file ID."""
        if data_bytes is None or not isinstance(data_bytes, bytes):
            raise RuntimeError(
                f"Критическая ошибка регистрации: для {name} не переданы байты содержимого (регистрация фиктивных данных запрещена)"
            )
        size = len(data_bytes)
        sha = sha256_bytes(data_bytes)
        if not file_id:
            if self.local_mode:
                file_id = f"local_id_{hashlib.md5(name.encode('utf-8')).hexdigest()[:12]}"
            else:
                # Получение file_id через rclone lsjson
                r = subprocess.run(
                    [RCLONE_BIN, "lsjson", "--stat", f"{self.remote_name}{name}"],
                    capture_output=True,
                    text=True,
                    env=self.rclone_env,
                    timeout=20,
                )
                if r.returncode != 0:
                    raise RuntimeError(f"Ошибка lsjson для {name}: код {r.returncode}")
                try:
                    d = json.loads(r.stdout or "{}")
                    file_id = d.get("ID") or ""
                except Exception as e:
                    raise RuntimeError(f"Ошибка парсинга lsjson для {name}: {e}")

        if not file_id or not str(file_id).strip():
            raise RuntimeError(f"Критическая ошибка изоляции: объект {name} не получил зарегистрированный file ID в Drive!")

        self.registry[name] = {
            "id": file_id,
            "name": name,
            "size": size,
            "sha256": sha,
            "created_at": time.time(),
        }
        self._save_manifest()
        return self.registry[name]

    def upload_synthetic_file(self, name: str, data: bytes) -> dict:
        """Загружает синтетический файл в тестовую папку через rclone rcat с предварительной проверкой бюджета."""
        if data is None or not isinstance(data, bytes):
            self.check_and_reserve_budget(None)
        self.check_and_reserve_budget(len(data))
        p = subprocess.Popen(
            [RCLONE_BIN, "rcat", f"{self.remote_name}{name}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.rclone_env,
        )
        _, err = p.communicate(input=data)
        if p.returncode != 0:
            raise RuntimeError(f"Ошибка загрузки {name} в Drive: {err.decode('utf-8', 'replace')}")

        return self.register_created_object(name, data)

    def run_parts_stage(
        self,
        parts_script: Path | str,
        obj_name: str,
        input_data: bytes,
        estimated_bytes: int | None = None,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess:
        """Выполняет parts stage с предварительным допуском и резервированием бюджета."""
        if estimated_bytes is None:
            if input_data is None or not isinstance(input_data, bytes):
                self.check_and_reserve_budget(None)
            estimated_bytes = len(input_data) + 4096
        self.check_and_reserve_budget(estimated_bytes)

        env = self.rclone_env.copy()
        res = subprocess.run(
            [sys.executable, str(parts_script), "stage", self.remote_name, obj_name],
            input=input_data,
            capture_output=True,
            env=env,
            timeout=timeout,
        )
        return res

    def sync_remote_objects(self, rclone_cmd: str | None = None) -> dict:
        """Синхронизирует созданные в хранилище объекты с локальным реестром стенда.
        При ошибке чтения или неполных данных выбрасывает явное исключение без добавления в реестр.
        """
        cmd = rclone_cmd or RCLONE_BIN
        listing = _get_remote_listing(self.remote_name, rclone_cmd=cmd)
        for it in listing:
            fname = it["Name"]
            if fname not in self.registry:
                cat_res = subprocess.run(
                    [cmd, "cat", f"{self.remote_name}{fname}"],
                    capture_output=True,
                    env=self.rclone_env,
                    timeout=30,
                )
                if cat_res.returncode != 0:
                    raise RuntimeError(
                        f"Ошибка чтения объекта {fname} из хранилища (код rclone {cat_res.returncode}): "
                        f"регистрация фиктивного содержимого запрещена, объект не добавлен в реестр"
                    )
                data_bytes = cat_res.stdout
                expected_size = it.get("Size")
                if expected_size is not None and len(data_bytes) != expected_size:
                    raise RuntimeError(
                        f"Размер объекта {fname} изменился во время чтения: прочитано {len(data_bytes)} Б, "
                        f"ожидалось {expected_size} Б. Отказ от регистрации по неполным данным."
                    )
                if self.registered_objects_size_sum + len(data_bytes) > self.MAX_UPLOAD_BYTES:
                    raise RuntimeError(
                        f"Отказ регистрации: превышение бюджета хранилища стенда "
                        f"({self.registered_objects_size_sum} + {len(data_bytes)} > {self.MAX_UPLOAD_BYTES} байт)."
                    )
                self.register_created_object(fname, data_bytes, file_id=it.get("ID"))
        return {it["Name"]: it for it in listing}



@unittest.skipUnless(os.environ.get("VG_TEST_REAL_DRIVE") == "1", "Требуется явное включение VG_TEST_REAL_DRIVE=1 для запуска на реальном Google Drive")
class TestStorageCleanerDrive(unittest.TestCase):
    """Интеграционные проверки безопасной очистки на настоящем Google Drive."""

    @classmethod
    def setUpClass(cls):
        print(f"\n[DriveStand] Инициализация тестовой папки на Google Drive...")
        cls.stand = DriveStandManager()
        print(f"[DriveStand] Создана тестовая папка: {cls.stand.folder_name}")
        print(f"[DriveStand] Folder ID: {cls.stand.folder_id}")
        print(f"[DriveStand] Account: {cls.stand.account_email}")
        print(f"[DriveStand] Ссылка на папку: {cls.stand.web_view_link}")

        cls.td = Path(tempfile.mkdtemp(prefix="wolf_drive_test_"))
        cls.parts_script = cls.td / "wolf-parts.py"
        cls.parts_script.write_text(WOLF_PARTS_CODE, encoding="utf-8")

        # На macOS shim для утилиты flock
        if not shutil.which("flock"):
            shim = cls.td / "flock"
            shim.write_text(
                "#!/usr/bin/env python3\n"
                "import sys, os, fcntl, time\n"
                "args = sys.argv[1:]\n"
                "nonblock = False\n"
                "wait_sec = None\n"
                "fd = None\n"
                "i = 0\n"
                "while i < len(args):\n"
                "    a = args[i]\n"
                "    if a == '-n':\n"
                "        nonblock = True\n"
                "    elif a in ('-w', '--wait'):\n"
                "        wait_sec = float(args[i+1])\n"
                "        i += 1\n"
                "    elif a.isdigit():\n"
                "        fd = int(a)\n"
                "    i += 1\n"
                "if fd is None: sys.exit(2)\n"
                "flags = fcntl.LOCK_EX\n"
                "if nonblock or wait_sec is not None: flags |= fcntl.LOCK_NB\n"
                "t0 = time.time()\n"
                "while True:\n"
                "    try:\n"
                "        fcntl.flock(fd, flags)\n"
                "        sys.exit(0)\n"
                "    except (BlockingIOError, OSError):\n"
                "        if nonblock or (wait_sec is not None and time.time() - t0 >= wait_sec):\n"
                "            sys.exit(1)\n"
                "        time.sleep(0.05)\n"
            )
            shim.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        # По требованию пользователя тестовая папка Drive и локальные журналы
        # сохраняются для приёмки:
        print(f"\n[DriveStand] Тестовые процессы завершены.")
        print(f"[DriveStand] Тестовая папка Google Drive сохранена для приёмки: {cls.stand.folder_id}")
        print(f"[DriveStand] Ссылка: {cls.stand.web_view_link}")
        print(f"[DriveStand] Локальный журнал стенда: {cls.stand.local_root}")
        shutil.rmtree(cls.td, ignore_errors=True)

    def setUp(self):
        self.sub_td = Path(tempfile.mkdtemp(dir=str(self.td)))
        self.l_dir = self.sub_td / "run_wolf"
        self.l_dir.mkdir(parents=True, exist_ok=True)
        self.t_dir = self.sub_td / "var_tmp_wolf"
        self.t_dir.mkdir(parents=True, exist_ok=True)
        self.s_dir = self.sub_td / "var_lib_wolf"
        self.s_dir.mkdir(parents=True, exist_ok=True)
        self.home_dir = self.sub_td / "home_user"
        self.home_dir.mkdir(parents=True, exist_ok=True)

        self.grant_file = self.l_dir / "cleanup_grant.json"

        # Запись активного сессионного допуска (с точным folder_id тестовой папки)
        grant_data = {
            "status": "granted",
            "run_id": self.stand.run_id,
            "iid": "999001",
            "folder_id": self.stand.folder_id,
            "granted_at": time.time(),
        }
        self.grant_file.write_text(json.dumps(grant_data, indent=2), encoding="utf-8")

        # Очистка хуков
        for hook_name in ("_test_hook", "_pre_verify_hook", "_post_classify_hook"):
            if hasattr(execute_stand_cleanup, hook_name):
                delattr(execute_stand_cleanup, hook_name)

    def tearDown(self):
        for hook_name in ("_test_hook", "_pre_verify_hook", "_post_classify_hook"):
            if hasattr(execute_stand_cleanup, hook_name):
                delattr(execute_stand_cleanup, hook_name)
        shutil.rmtree(self.sub_td, ignore_errors=True)

    def _create_real_wolf_runner(self, home_dir: Path, part_bytes: int | None = None):
        """Создаёт runner со штатными функциями Wolf, подключёнными к vgtest: на Google Drive."""
        self.current_part_bytes = part_bytes
        names = (
            "st", "rd", "spec", "F", "ls_", "man", "sz", "delsz", "gamearch",
            "rver", "rl", "idhint", "pack", "get", "del", "xt_user", "unpack",
        )
        body = "\n".join(wolf_func(n) for n in names).replace("/usr/local/bin/wolf-parts.py", str(self.parts_script))
        part_bytes_def = f'WOLF_PART_BYTES="{part_bytes}"\nexport WOLF_PART_BYTES' if part_bytes else 'unset WOLF_PART_BYTES'
        header = f"""#!/bin/bash
S="{self.s_dir}"; T="{self.t_dir}"; L="{self.l_dir}"; R="{self.stand.remote_name}"
DH="{home_dir}"
SR=sa; ZL=3; ZG=1; LS="$T/ls"
mkdir -p "$S"/h "$S"/m "$S"/v "$S"/p "$S"/st "$S"/nt "$S"/nm "$S"/x "$S"/pg "$T" "$L"
WOLF_TMP="{self.t_dir}"
export WOLF_TMP
NOW=1
export NOW
{part_bytes_def}
rc()  {{ "{RCLONE_BIN}" "$@" --retries=5 --low-level-retries=20 --drive-use-trash=false --drive-pacer-min-sleep=10ms; }}
rcp() {{ "{RCLONE_BIN}" "$@" --retries=5 --low-level-retries=20 --drive-use-trash=false --drive-pacer-min-sleep=10ms; }}
log() {{ :; }}
u()   {{ "$@"; }}
sr()  {{ SR=sa; }}
parts() {{
  local c=$1; shift
  WOLF_TMP="$T" python3 "{self.parts_script}" "$c" "$R" "$@"
}}
"""
        footer = """
case "$1" in
  pack)
    shift
    rl > "$LS"
    pack "$@"
    ;;
  unpack)
    shift
    rm -f "$S/v/$1"
    rl > "$LS"
    unpack "$1"
    ;;
  parts)
    shift
    python3 "$PARTS_SCRIPT" "$@"
    ;;
  *)
    "$@"
    ;;
esac
"""
        runner = self.sub_td / "wolf_runner.sh"
        runner.write_text(header + "\nPARTS_SCRIPT=\"" + str(self.parts_script) + "\"\n" + body + "\n" + footer, encoding="utf-8")
        runner.chmod(0o755)
        return runner

    def _run_wolf(self, runner: Path, *args, estimated_upload_bytes: int | None = None):
        cmd = args[0] if args else ""
        if cmd in ("pack", "stage_delta"):
            archive_name = args[1] if len(args) > 1 else ""
            if estimated_upload_bytes is None:
                part_bytes_val = getattr(self, "current_part_bytes", None)
                estimated_upload_bytes = self.stand.estimate_archive_upload_bound(
                    self.home_dir, archive_name, part_bytes=part_bytes_val
                )
            self.stand.check_and_reserve_budget(estimated_upload_bytes)

        env = self.stand.rclone_env.copy()
        gnubin = [
            "/opt/homebrew/opt/findutils/libexec/gnubin",
            "/opt/homebrew/opt/gnu-tar/libexec/gnubin",
            "/opt/homebrew/opt/coreutils/libexec/gnubin",
            "/usr/local/opt/findutils/libexec/gnubin",
            "/usr/local/opt/gnu-tar/libexec/gnubin",
            "/usr/local/opt/coreutils/libexec/gnubin",
        ]
        path_items = [str(self.td), str(self.sub_td)]
        path_items.extend([p for p in gnubin if os.path.isdir(p)])
        if ZSTD_BIN:
            path_items.append(str(Path(ZSTD_BIN).parent))
        if RCLONE_BIN:
            path_items.append(str(Path(RCLONE_BIN).parent))
        path_items.append(env.get("PATH", ""))
        env["PATH"] = ":".join(path_items)

        res = subprocess.run([str(runner), *args], capture_output=True, text=True, env=env, timeout=120)
        return res

    # -------------------------------------------------------------------------
    # Сценарий А: Одиночная база, активная дельта, устаревшая дельта
    # -------------------------------------------------------------------------
    def test_scenario_a_single_base_delta_and_superseded_delta(self):
        """Сценарий А: Одиночная база и активная дельта, рядом устаревшая дельта.
        После очистки настоящий unpack восстанавливает данные (включая удаление файла).
        """
        print("\n--- [Сценарий А] Одиночная база + активная дельта + устаревшая дельта ---")
        game_dir = self.home_dir / "sa" / "steamapps" / "common" / "DriveGameA"
        game_dir.mkdir(parents=True, exist_ok=True)

        # 1. Формирование базы (40 КБ unchanged, 2 КБ modified v1, 1 КБ deleted)
        unchanged_bytes = b"UNCHANGED_DRIVE_A_DATA_" * 1600  # ~38.4 КБ
        mod_v1_bytes = b"MODIFIED_DRIVE_A_V1_" * 100         # ~2 КБ
        del_bytes = b"DELETED_DRIVE_A_DATA_" * 50           # ~1 КБ

        (game_dir / "unchanged.dat").write_bytes(unchanged_bytes)
        (game_dir / "modified.dat").write_bytes(mod_v1_bytes)
        (game_dir / "deleted.dat").write_bytes(del_bytes)

        runner = self._create_real_wolf_runner(self.home_dir, part_bytes=None)
        r_pack1 = self._run_wolf(runner, "pack", "sgame--DriveGameA")
        self.assertEqual(r_pack1.returncode, 0, f"pack v1 failed: {r_pack1.stderr}")

        # Регистрация созданного base объекта в журнале стенда
        base_tar_name = "sgame--DriveGameA.tar.zst"
        cat_res = subprocess.run([RCLONE_BIN, "cat", f"{self.stand.remote_name}{base_tar_name}"],
                                 capture_output=True, env=self.stand.rclone_env, timeout=30)
        self.assertEqual(cat_res.returncode, 0)
        self.stand.register_created_object(base_tar_name, cat_res.stdout)

        # 2. Формирование дельты (modified v2, new_file, deleted удалён)
        mod_v2_bytes = b"MODIFIED_DRIVE_A_V2_NEW_BYTES_" * 70  # ~2.1 КБ
        new_file_bytes = b"NEW_FILE_DRIVE_A_DATA_" * 50         # ~1.1 КБ

        (game_dir / "modified.dat").write_bytes(mod_v2_bytes)
        (game_dir / "new_file.dat").write_bytes(new_file_bytes)
        (game_dir / "deleted.dat").unlink()

        r_pack2 = self._run_wolf(runner, "pack", "sgame--DriveGameA")
        self.assertEqual(r_pack2.returncode, 0, f"pack v2 delta failed: {r_pack2.stderr}")

        # Поиск созданной дельты в Drive
        listing = _get_remote_listing(self.stand.remote_name, rclone_cmd=RCLONE_BIN)
        delta_obj = None
        for it in listing:
            if it["Name"].startswith(f"{base_tar_name}.") and not it["Name"].endswith(".parts"):
                delta_obj = it["Name"]
                break
        self.assertIsNotNone(delta_obj, "Дельта не была найдена в хранилище Drive!")

        # Регистрация дельты в журнале стенда
        cat_d = subprocess.run([RCLONE_BIN, "cat", f"{self.stand.remote_name}{delta_obj}"],
                               capture_output=True, env=self.stand.rclone_env, timeout=30)
        self.assertEqual(cat_d.returncode, 0)
        self.stand.register_created_object(delta_obj, cat_d.stdout)

        # 3. Загрузка устаревшей дельты (кандидат)
        deadbeef_name = f"{base_tar_name}.deadbeef"
        deadbeef_bytes = b"OBSOLETE_DELTA_CANDIDATE_DATA" * 50
        self.stand.upload_synthetic_file(deadbeef_name, deadbeef_bytes)

        # 4. Предварительный просмотр (preview)
        preview_report = classify_archive(
            remote=self.stand.remote_name,
            obj_name="sgame--DriveGameA",
            grant_file=str(self.grant_file),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            rclone_cmd=RCLONE_BIN,
        )
        self.assertTrue(preview_report.get("cleanup_allowed"), f"Preview report blocked: {preview_report}")
        report_files = {f["name"]: f["category"] for f in preview_report.get("files", [])}

        self.assertEqual(report_files.get(base_tar_name), "ACTIVE")
        self.assertEqual(report_files.get(delta_obj), "ACTIVE")
        self.assertEqual(report_files.get(deadbeef_name), "CANDIDATE")

        # Сохранение отчёта локально
        rep_path = self.stand.reports_dir / "scenario_a_preview.json"
        rep_path.write_text(json.dumps(preview_report, indent=2, ensure_ascii=False), encoding="utf-8")

        # 5. Исполнение очистки в режиме Drive
        clean_res = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameA",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(clean_res["status"], "completed")
        self.assertEqual(len(clean_res["deleted"]), 1)
        self.assertEqual(clean_res["deleted"][0]["name"], deadbeef_name)

        # Проверка судьбы в Drive: кандидат удалён
        self.assertTrue(self.stand.adapter.verify_deleted(deadbeef_name, self.stand.registry[deadbeef_name]))

        # 6. Восстановление настоящим unpack() Wolf в пустой каталог
        shutil.rmtree(game_dir)
        r_unpack = self._run_wolf(runner, "unpack", "sgame--DriveGameA")
        self.assertEqual(r_unpack.returncode, 0, f"unpack failed: {r_unpack.stderr}")

        restored_hashes = get_dir_tree_hashes(game_dir)
        self.assertEqual(restored_hashes.get("unchanged.dat"), sha256_bytes(unchanged_bytes))
        self.assertEqual(restored_hashes.get("modified.dat"), sha256_bytes(mod_v2_bytes))
        self.assertEqual(restored_hashes.get("new_file.dat"), sha256_bytes(new_file_bytes))
        self.assertNotIn("deleted.dat", restored_hashes)
        self.assertNotIn(".wolf-del.sgame--DriveGameA", restored_hashes)
        print("[Сценарий А] Успешно: кандидат удалён из Drive, данные восстановлены 1:1.")

    # -------------------------------------------------------------------------
    # Сценарий Б: База и дельта из частей (parts)
    # -------------------------------------------------------------------------
    def test_scenario_b_parts_base_and_delta_with_superseded_and_unknown_gen(self):
        """Сценарий Б: База и дельта из частей.
        Активные оглавления и все используемые части остаются.
        Неизвестное поколение сохраняется (PROTECTED).
        Удаляются только доказанные кандидаты вытесненного поколения.
        """
        print("\n--- [Сценарий Б] Многотомная база/дельта + неизвестное поколение (PROTECTED) ---")
        game_dir = self.home_dir / "sa" / "steamapps" / "common" / "DriveGameB"
        game_dir.mkdir(parents=True, exist_ok=True)

        data_b1 = b"PARTS_BASE_DATA_PART_1_" * 120  # ~2.8 КБ
        data_b2 = b"PARTS_BASE_DATA_PART_2_" * 120  # ~2.8 КБ
        (game_dir / "part1.dat").write_bytes(data_b1)
        (game_dir / "part2.dat").write_bytes(data_b2)

        runner = self._create_real_wolf_runner(self.home_dir, part_bytes=1000)
        r_pack = self._run_wolf(runner, "pack", "sgame--DriveGameB")
        self.assertEqual(r_pack.returncode, 0, f"pack parts failed: {r_pack.stderr}")

        # Регистрация созданных частей базы в Drive
        listing = _get_remote_listing(self.stand.remote_name, rclone_cmd=RCLONE_BIN)
        base_obj = "sgame--DriveGameB.tar.zst"
        for it in listing:
            if it["Name"].startswith(base_obj):
                cat_p = subprocess.run([RCLONE_BIN, "cat", f"{self.stand.remote_name}{it['Name']}"],
                                       capture_output=True, env=self.stand.rclone_env, timeout=30)
                if cat_p.returncode != 0:
                    raise RuntimeError(f"Cannot read stand object {fname}: rclone exit {cat_p.returncode}")
                p_bytes = cat_p.stdout
                if len(p_bytes) != it.get("Size"):
                    raise RuntimeError(f"Stand object size changed while reading {fname}")
                self.stand.register_created_object(it["Name"], p_bytes, file_id=it.get("ID"))

        # Создание вытесненной дельты из частей к старой базе (доказанные кандидаты)
        old_part1 = f"{base_obj}.deadbeef.g11111111-0001"
        old_part2 = f"{base_obj}.deadbeef.g11111111-0002"
        self.stand.upload_synthetic_file(old_part1, b"OLD_PART_1_DATA" * 50)
        self.stand.upload_synthetic_file(old_part2, b"OLD_PART_2_DATA" * 50)

        # Создание неизвестного поколения базы без оглавления (должно быть PROTECTED!)
        unk_part = f"{base_obj}.g22222222-0001"
        self.stand.upload_synthetic_file(unk_part, b"UNKNOWN_GEN_PART_DATA" * 50)

        # Предварительный просмотр
        preview_report = classify_archive(
            remote=self.stand.remote_name,
            obj_name="sgame--DriveGameB",
            grant_file=str(self.grant_file),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            rclone_cmd=RCLONE_BIN,
        )
        report_files = {f["name"]: f["category"] for f in preview_report.get("files", [])}

        self.assertEqual(report_files.get(old_part1), "CANDIDATE")
        self.assertEqual(report_files.get(old_part2), "CANDIDATE")
        self.assertEqual(report_files.get(unk_part), "PROTECTED")  # Неизвестное поколение защищено!

        # Исполнение очистки
        clean_res = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameB",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(clean_res["status"], "completed")
        deleted_names = [f["name"] for f in clean_res["deleted"]]
        self.assertIn(old_part1, deleted_names)
        self.assertIn(old_part2, deleted_names)
        self.assertNotIn(unk_part, deleted_names)

        # Проверка сохранности неизвестного поколения в Drive
        self.assertFalse(self.stand.adapter.verify_deleted(unk_part, self.stand.registry[unk_part]))

        # Восстановление через unpack
        shutil.rmtree(game_dir)
        r_unpack = self._run_wolf(runner, "unpack", "sgame--DriveGameB")
        self.assertEqual(r_unpack.returncode, 0, f"unpack failed: {r_unpack.stderr}")

        restored_hashes = get_dir_tree_hashes(game_dir)
        self.assertEqual(restored_hashes.get("part1.dat"), sha256_bytes(data_b1))
        self.assertEqual(restored_hashes.get("part2.dat"), sha256_bytes(data_b2))
        print("[Сценарий Б] Успешно: вытесненные части удалены, неизвестное поколение защищено, восстановление 100%.")

    # -------------------------------------------------------------------------
    # Сценарий В: Незавершённые попытки (attempt/pending) + resume
    # -------------------------------------------------------------------------
    def test_scenario_c_uncompleted_attempts_protected_and_resumed(self):
        """Сценарий В: Незавершённые попытки базы/дельты защищены.
        Очистка сохраняет их (0 удалений). Настоящий resume завершается.
        """
        print("\n--- [Сценарий В] Защита незавершённых попыток (attempt/pending) + штатный resume ---")
        base_obj = "sgame--DriveGameC.tar.zst"
        staged_part = f"{base_obj}.g33333333-0001"
        c_payload = b"SCENARIO_C_PAYLOAD_DATA_" * 60
        staged_bytes = make_tar_zst({"sa/steamapps/common/DriveGameC/c_file.dat": c_payload})
        self.stand.upload_synthetic_file(staged_part, staged_bytes)

        # Создание .parts.new в хранилище
        staged_idx_content = (
            f"vastgame-parts 1\n"
            f"{len(staged_bytes)}\n"
            f"{staged_part}\t{md5_bytes(staged_bytes)}\t{len(staged_bytes)}\n"
        ).encode("utf-8")
        staged_idx_name = f"{base_obj}.parts.new"
        self.stand.upload_synthetic_file(staged_idx_name, staged_idx_content)

        # Регистрация attempt в $T
        att_file = self.t_dir / f"attempt-{base_obj.replace('/', '_')}"
        att_receipt = _WOLF_PARTS["format_receipt"]((
            "parts",
            "33333333",
            md5_bytes(staged_idx_content),
            len(staged_idx_content),
            [(staged_part, md5_bytes(staged_bytes), len(staged_bytes))],
            time.time(),
        ))
        att_file.write_text(f"object {base_obj}\n{att_receipt}", encoding="utf-8")

        # Регистрация pending в $S/pending
        p_dir = self.s_dir / "pending"
        p_dir.mkdir(parents=True, exist_ok=True)
        pending_file = p_dir / "sgame--DriveGameC"
        pending_file.write_text(f"object {base_obj}\n{att_receipt}", encoding="utf-8")

        # 1. Предварительный просмотр
        preview_report = classify_archive(
            remote=self.stand.remote_name,
            obj_name="sgame--DriveGameC",
            grant_file=str(self.grant_file),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            rclone_cmd=RCLONE_BIN,
        )
        # Все файлы попытки должны быть PROTECTED
        for f in preview_report.get("files", []):
            self.assertEqual(f["category"], "PROTECTED", f"Файл {f['name']} не PROTECTED: {f['category']}")

        # 2. Исполнение очистки -> 0 удалений
        clean_res = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameC",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(clean_res["status"], "noop")
        self.assertEqual(len(clean_res["deleted"]), 0)

        # 3. Штатный resume
        runner = self._create_real_wolf_runner(self.home_dir, part_bytes=None)
        env = self.stand.rclone_env.copy()
        env["WOLF_TMP"] = str(self.t_dir)
        r_resume = subprocess.run(
            [sys.executable, str(self.parts_script), "resume", self.stand.remote_name, base_obj],
            input=att_receipt.encode("utf-8"),
            capture_output=True,
            text=False,
            env=env,
            timeout=30,
        )
        self.assertEqual(r_resume.returncode, 0, f"resume failed: {r_resume.stderr}")

        # Проверка, что .parts.new переименован в .parts в Drive
        cat_idx = subprocess.run([RCLONE_BIN, "cat", f"{self.stand.remote_name}{base_obj}.parts"],
                                 capture_output=True, env=self.stand.rclone_env, timeout=15)
        self.assertEqual(cat_idx.returncode, 0)

        # 4. Восстановление опубликованных данных через unpack
        game_dir_c = self.home_dir / "sa" / "steamapps" / "common" / "DriveGameC"
        shutil.rmtree(game_dir_c, ignore_errors=True)
        r_unpack_c = self._run_wolf(runner, "unpack", "sgame--DriveGameC")
        self.assertEqual(r_unpack_c.returncode, 0, f"unpack scenario C failed: {r_unpack_c.stderr}")
        self.assertEqual(get_dir_tree_hashes(game_dir_c).get("c_file.dat"), sha256_bytes(c_payload))
        print("[Сценарий В] Успешно: незавершённая попытка защищена (0 удалений), resume завершил публикацию, unpack восстановил данные.")

    # -------------------------------------------------------------------------
    # Сценарий Г: Отрицательные тесты (0 удалений)
    # -------------------------------------------------------------------------
    def test_scenario_d_negative_authorizations_receipts_and_network_errors(self):
        """Сценарий Г: Отрицательные тесты (ноль удалений).
        - Нет разрешения;
        - Устаревший run_id;
        - Чужой folder_id / iid;
        - Повреждённая квитанция;
        - Ошибка связи в тестовом адаптере.
        """
        print("\n--- [Сценарий Г] Отрицательные проверки (гарантия 0 удалений) ---")
        base_obj = "sgame--DriveGameD.tar.zst"
        cand_name = f"{base_obj}.deadbeef"
        self.stand.upload_synthetic_file(base_obj, b"BASE_DATA_FOR_NEG" * 50)
        self.stand.upload_synthetic_file(cand_name, b"CAND_DATA_FOR_NEG" * 50)

        # 1. Нет файла допуска
        no_grant_path = self.sub_td / "no_grant.json"
        res1 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameD",
            grant_file=str(no_grant_path),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(res1["status"], "forbidden")
        self.assertEqual(len(res1["deleted"]), 0)

        # 2. Устаревший run_id
        res2 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameD",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val="stale_run_id_12345",
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(res2["status"], "forbidden")
        self.assertEqual(len(res2["deleted"]), 0)

        # 3. Чужой folder_id
        res3 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameD",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val="foreign_folder_999",
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(res3["status"], "rejected")
        self.assertEqual(len(res3["deleted"]), 0)

        # 4. Повреждённая квитанция
        corrupt_att = self.t_dir / f"attempt-{base_obj.replace('/', '_')}"
        corrupt_att.write_text("CORRUPTED_HEADER_INVALID\n", encoding="utf-8")
        res4 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameD",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertIn(res4["status"], ("forbidden", "aborted"))
        self.assertEqual(len(res4["deleted"]), 0)
        corrupt_att.unlink()

        # 5. Ошибка связи в тестовом адаптере
        self.stand.adapter.mock_network_error = True
        self.stand.adapter.mock_network_error_op = "delete"
        try:
            res5 = execute_stand_cleanup(
                stand_dir=self.stand.remote_name,
                obj_name="sgame--DriveGameD",
                grant_file=str(self.grant_file),
                lock_dir=str(self.l_dir),
                tmp_dir=str(self.t_dir),
                state_dir=str(self.s_dir),
                run_id_val=self.stand.run_id,
                iid_val="999001",
                folder_id_val=self.stand.folder_id,
                drive_test_mode=True,
                drive_test_folder_id=self.stand.folder_id,
                drive_test_registry=self.stand.registry,
                rclone_cmd=RCLONE_BIN,
                drive_adapter=self.stand.adapter,
            )
            self.assertEqual(res5["status"], "stopped")
            self.assertEqual(len(res5["deleted"]), 0)
        finally:
            self.stand.adapter.mock_network_error = False

        # Проверка, что файл остался на месте
        self.assertFalse(self.stand.adapter.verify_deleted(cand_name, self.stand.registry[cand_name]))
        print("[Сценарий Г] Успешно: во всех негативных сценариях строго ноль удалений.")

    # -------------------------------------------------------------------------
    # Сценарий Д: Прерывание после удаления первого кандидата и повтор
    # -------------------------------------------------------------------------
    def test_scenario_e_interruption_after_first_candidate_and_resume(self):
        """Сценарий Д: Прерывание после удаления 1-го кандидата.
        Сохранение промежуточного результата, проверка восстановления активных данных,
        повторный проход очистки остатка.
        """
        print("\n--- [Сценарий Д] Прерывание прохода очистки и безопасный повтор ---")
        base_obj = "sgame--DriveGameE.tar.zst"
        cand1 = f"{base_obj}.deadbeef"
        cand2 = f"{base_obj}.cafebabe"

        # Загрузка базы через реальный pack
        game_dir = self.home_dir / "sa" / "steamapps" / "common" / "DriveGameE"
        game_dir.mkdir(parents=True, exist_ok=True)
        game_data = b"DRIVE_E_GAME_CONTENT_" * 100
        (game_dir / "file.dat").write_bytes(game_data)

        runner = self._create_real_wolf_runner(self.home_dir, part_bytes=None)
        r_pack = self._run_wolf(runner, "pack", "sgame--DriveGameE")
        self.assertEqual(r_pack.returncode, 0)

        # Регистрация базы
        cat_b = subprocess.run([RCLONE_BIN, "cat", f"{self.stand.remote_name}{base_obj}"],
                               capture_output=True, env=self.stand.rclone_env, timeout=30)
        self.assertEqual(cat_b.returncode, 0)
        self.stand.register_created_object(base_obj, cat_b.stdout)

        # Загрузка 2 кандидатов
        self.stand.upload_synthetic_file(cand1, b"CAND_1_DATA" * 50)
        self.stand.upload_synthetic_file(cand2, b"CAND_2_DATA" * 50)

        # Хук прерывания после 1-го кандидата
        def stop_after_first(fname, idx):
            if idx == 1:
                raise RuntimeError("simulated crash after candidate 0")

        execute_stand_cleanup._test_hook = stop_after_first

        # Проход 1 (с прерыванием)
        res1 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameE",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(res1["status"], "stopped")
        self.assertEqual(len(res1["deleted"]), 1)
        deleted_name = res1["deleted"][0]["name"]
        self.assertEqual(len(res1["remaining"]), 1)
        remaining_name = res1["remaining"][0]["name"]
        self.assertIn(deleted_name, (cand1, cand2))
        self.assertIn(remaining_name, (cand1, cand2))
        self.assertNotEqual(deleted_name, remaining_name)

        # Проверка Drive: удалённый кандидат отсутствует, оставшийся на месте
        self.assertTrue(self.stand.adapter.verify_deleted(deleted_name, self.stand.registry[deleted_name]))
        self.assertFalse(self.stand.adapter.verify_deleted(remaining_name, self.stand.registry[remaining_name]))

        # Проверка восстановления активных данных в промежуточной точке
        shutil.rmtree(game_dir)
        r_unpack1 = self._run_wolf(runner, "unpack", "sgame--DriveGameE")
        self.assertEqual(r_unpack1.returncode, 0)
        self.assertEqual(get_dir_tree_hashes(game_dir).get("file.dat"), sha256_bytes(game_data))

        # Снимаем хук прерывания
        delattr(execute_stand_cleanup, "_test_hook")

        # Проход 2 (завершение остатка)
        res2 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameE",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(res2["status"], "completed")
        self.assertEqual(len(res2["deleted"]), 1)
        self.assertEqual(res2["deleted"][0]["name"], remaining_name)
        self.assertEqual(len(res2["remaining"]), 0)

        # Проверка Drive: оба кандидата удалены
        self.assertTrue(self.stand.adapter.verify_deleted(cand1, self.stand.registry[cand1]))
        self.assertTrue(self.stand.adapter.verify_deleted(cand2, self.stand.registry[cand2]))

        # Финальное восстановление
        shutil.rmtree(game_dir)
        r_unpack2 = self._run_wolf(runner, "unpack", "sgame--DriveGameE")
        self.assertEqual(r_unpack2.returncode, 0)
        self.assertEqual(get_dir_tree_hashes(game_dir).get("file.dat"), sha256_bytes(game_data))
        print("[Сценарий Д] Успешно: промежуточный результат зафиксирован, повторный проход завершил очистку.")

    # -------------------------------------------------------------------------
    # Сценарий Е: Изменение тестовых метаданных после предварительного отчёта
    # -------------------------------------------------------------------------
    def test_scenario_f_metadata_snapshot_drift_aborts_cleanup(self):
        """Сценарий Е: Изменение метаданных в Drive после предварительного отчёта.
        Старый план отменяется (status: aborted, reason: metadata_snapshot_mismatch),
        ноль удалений.
        """
        print("\n--- [Сценарий Е] Изменение метаданных Drive после предварительного отчёта ---")
        base_obj = "sgame--DriveGameF.tar.zst"
        cand_name = f"{base_obj}.deadbeef"

        self.stand.upload_synthetic_file(base_obj, b"BASE_DATA_FOR_DRIFT" * 50)
        self.stand.upload_synthetic_file(cand_name, b"CAND_DATA_FOR_DRIFT" * 50)

        # Хук: появление непредусмотренного файла между классификацией и проверкой снимка
        def mutate_drive_before_verify():
            unexpected_file = f"{base_obj}.unexpected_file"
            self.stand.upload_synthetic_file(unexpected_file, b"UNEXPECTED_DATA" * 10)

        execute_stand_cleanup._pre_verify_hook = mutate_drive_before_verify

        res = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameF",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(res["status"], "aborted")
        self.assertIn("metadata_snapshot_mismatch", res["reason"])
        self.assertEqual(len(res["deleted"]), 0)

        # Кандидат не был удалён
        self.assertFalse(self.stand.adapter.verify_deleted(cand_name, self.stand.registry[cand_name]))
        print("[Сценарий Е] Успешно: план отменён при расхождении метаданных, 0 удалений.")



@unittest.skipUnless(os.environ.get("VG_TEST_REAL_DRIVE") == "1", "Требуется явное включение VG_TEST_REAL_DRIVE=1 для запуска на реальном Google Drive")
class TestStorageCleanerDriveRefinedBC(unittest.TestCase):
    """Уточнённая интеграционная проверка сценариев Б и В на Google Drive.

    Выполняется в изолированной дочерней папке внутри родительской тестовой папки:
    parent_folder_id = "1vrAvD-o3SDkliAdksSKX7IrBvGDPFGGI".
    Никакие пользовательские архивы и рабочая папка vastai-cloud-games не затрагиваются.
    Лимит 100 МиБ строго соблюдается.
    """

    PARENT_STAND_ID = "1vrAvD-o3SDkliAdksSKX7IrBvGDPFGGI"

    @classmethod
    def setUpClass(cls):
        print(f"\n[RefinedDriveStand] Инициализация дочерней тестовой папки внутри {cls.PARENT_STAND_ID}...")
        cls.stand = DriveStandManager(parent_folder_id=cls.PARENT_STAND_ID)
        print(f"[RefinedDriveStand] Создана дочерняя папка: {cls.stand.folder_name}")
        print(f"[RefinedDriveStand] Child Folder ID: {cls.stand.folder_id}")
        print(f"[RefinedDriveStand] Parent Folder ID: {cls.stand.parent_folder_id}")
        print(f"[RefinedDriveStand] Account: {cls.stand.account_email}")
        print(f"[RefinedDriveStand] Ссылка на папку: {cls.stand.web_view_link}")

        cls.td = Path(tempfile.mkdtemp(prefix="wolf_drive_refined_"))
        cls.parts_script = cls.td / "wolf-parts.py"
        cls.parts_script.write_text(WOLF_PARTS_CODE, encoding="utf-8")

        # На macOS shim для flock
        if not shutil.which("flock"):
            shim = cls.td / "flock"
            shim.write_text(
                "#!/usr/bin/env python3\n"
                "import sys, os, fcntl, time\n"
                "args = sys.argv[1:]\n"
                "nonblock = False\n"
                "wait_sec = None\n"
                "fd = None\n"
                "i = 0\n"
                "while i < len(args):\n"
                "    a = args[i]\n"
                "    if a == '-n':\n"
                "        nonblock = True\n"
                "    elif a in ('-w', '--wait'):\n"
                "        wait_sec = float(args[i+1])\n"
                "        i += 1\n"
                "    elif a.isdigit():\n"
                "        fd = int(a)\n"
                "    i += 1\n"
                "if fd is None: sys.exit(2)\n"
                "flags = fcntl.LOCK_EX\n"
                "if nonblock or wait_sec is not None: flags |= fcntl.LOCK_NB\n"
                "t0 = time.time()\n"
                "while True:\n"
                "    try:\n"
                "        fcntl.flock(fd, flags)\n"
                "        sys.exit(0)\n"
                "    except (BlockingIOError, OSError):\n"
                "        if nonblock or (wait_sec is not None and time.time() - t0 >= wait_sec):\n"
                "            sys.exit(1)\n"
                "        time.sleep(0.05)\n"
            )
            shim.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        # Итоговая фиксация сводки стенда
        reg_sum = cls.stand.registered_objects_size_sum
        del_bytes = cls.stand.deleted_bytes_candidates
        rem_sum = cls.stand.remaining_objects_size_sum
        run_summary = {
            "run_id": cls.stand.run_id,
            "folder_name": cls.stand.folder_name,
            "folder_id": cls.stand.folder_id,
            "parent_folder_id": cls.stand.parent_folder_id,
            "account_email": cls.stand.account_email,
            "web_view_link": cls.stand.web_view_link,
            "registered_objects_size_sum": reg_sum,
            "deleted_bytes_candidates": del_bytes,
            "remaining_objects_size_sum": rem_sum,
            "network_uploaded_bytes": None,
            "network_measurement_status": "unmeasured (сетевой трафик сокетов, HTTP-заголовки и повторы передачи не измерялись; учитываются размеры содержимого объектов)",
            "scenarios_verified": ["Scenario_B_refined", "Scenario_C_refined"],
            "created_at": time.time(),
        }
        summary_path = cls.stand.local_root / "run_summary.json"
        summary_path.write_text(json.dumps(run_summary, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"\n[RefinedDriveStand] Тестирование сценариев Б и В завершено.")
        print(f"[RefinedDriveStand] Дочерняя папка Google Drive сохранена для приёмки: {cls.stand.folder_id}")
        print(f"[RefinedDriveStand] Ссылка: {cls.stand.web_view_link}")
        print(f"[RefinedDriveStand] Локальный каталог стенда: {cls.stand.local_root}")
        print(f"[RefinedDriveStand] Сумма зарегистрированных объектов: {reg_sum} байт")
        print(f"[RefinedDriveStand] Удалено кандидатов: {del_bytes} байт")
        print(f"[RefinedDriveStand] Оставшиеся объекты в хранилище: {rem_sum} байт")
        print(f"[RefinedDriveStand] Сетевой объём: не измерялся (network_uploaded_bytes: None)")
        shutil.rmtree(cls.td, ignore_errors=True)

    def setUp(self):
        self.sub_td = Path(tempfile.mkdtemp(dir=str(self.td)))
        self.l_dir = self.sub_td / "run_wolf"
        self.l_dir.mkdir(parents=True, exist_ok=True)
        self.t_dir = self.sub_td / "var_tmp_wolf"
        self.t_dir.mkdir(parents=True, exist_ok=True)
        self.s_dir = self.sub_td / "var_lib_wolf"
        self.s_dir.mkdir(parents=True, exist_ok=True)
        self.home_dir = self.sub_td / "home_user"
        self.home_dir.mkdir(parents=True, exist_ok=True)

        self.grant_file = self.l_dir / "cleanup_grant.json"
        grant_data = {
            "status": "granted",
            "run_id": self.stand.run_id,
            "iid": "999001",
            "folder_id": self.stand.folder_id,
            "granted_at": time.time(),
        }
        self.grant_file.write_text(json.dumps(grant_data, indent=2), encoding="utf-8")

        for hook_name in ("_test_hook", "_pre_verify_hook", "_post_classify_hook"):
            if hasattr(execute_stand_cleanup, hook_name):
                delattr(execute_stand_cleanup, hook_name)

    def tearDown(self):
        for hook_name in ("_test_hook", "_pre_verify_hook", "_post_classify_hook"):
            if hasattr(execute_stand_cleanup, hook_name):
                delattr(execute_stand_cleanup, hook_name)
        shutil.rmtree(self.sub_td, ignore_errors=True)

    def _create_real_wolf_runner(self, home_dir: Path, part_bytes: int | None = None):
        """Создаёт runner со штатными функциями Wolf, подключёнными к vgtest: на Google Drive."""
        self.current_part_bytes = part_bytes
        names = (
            "st", "rd", "spec", "F", "ls_", "man", "sz", "delsz", "gamearch",
            "rver", "rl", "idhint", "pack", "get", "del", "xt_user", "unpack",
        )
        body = "\n".join(wolf_func(n) for n in names).replace("/usr/local/bin/wolf-parts.py", str(self.parts_script))
        part_bytes_def = f'WOLF_PART_BYTES="{part_bytes}"\nexport WOLF_PART_BYTES' if part_bytes else 'unset WOLF_PART_BYTES'
        header = f"""#!/bin/bash
S="{self.s_dir}"; T="{self.t_dir}"; L="{self.l_dir}"; R="{self.stand.remote_name}"
DH="{home_dir}"
SR=sa; ZL=3; ZG=1; LS="$T/ls"
mkdir -p "$S"/h "$S"/m "$S"/v "$S"/p "$S"/st "$S"/nt "$S"/nm "$S"/x "$S"/pg "$T" "$L"
WOLF_TMP="{self.t_dir}"
export WOLF_TMP
NOW=1
export NOW
{part_bytes_def}
rc()  {{ "{RCLONE_BIN}" "$@" --retries=5 --low-level-retries=20 --drive-use-trash=false --drive-pacer-min-sleep=10ms; }}
rcp() {{ "{RCLONE_BIN}" "$@" --retries=5 --low-level-retries=20 --drive-use-trash=false --drive-pacer-min-sleep=10ms; }}
log() {{ :; }}
u()   {{ "$@"; }}
sr()  {{ SR=sa; }}
parts() {{
  local c=$1; shift
  WOLF_TMP="$T" python3 "{self.parts_script}" "$c" "$R" "$@"
}}
stage_delta() {{
  local n=$1
  local o=$2
  spec "$n"
  man "$n" > "$T/c"
  LC_ALL=C comm -z13 "$S/m/$n" "$T/c" > "$T/d"
  LC_ALL=C comm -z23 <(cut -zf1 "$S/m/$n") <(cut -zf1 "$T/c") > "$T/.wolf-del.$n"
  cut -zf1 "$T/d" > "$T/l"
  local -a x=()
  [ -s "$T/.wolf-del.$n" ] && x=(-C "$T" ".wolf-del.$n")
  tar -cf - -C "$B" --no-recursion --null -T "$T/l" "${{x[@]}}" \
    --warning=no-file-changed --warning=no-file-removed \
    | zstd -q -T0 -1 \
    | parts stage "$o"
}}
"""
        footer = """
case "$1" in
  pack)
    shift
    rl > "$LS"
    pack "$@"
    ;;
  unpack)
    shift
    rm -f "$S/v/$1"
    rl > "$LS"
    unpack "$1"
    ;;
  parts)
    shift
    python3 "$PARTS_SCRIPT" "$@"
    ;;
  stage_delta)
    shift
    stage_delta "$@"
    ;;
  *)
    "$@"
    ;;
esac
"""
        runner = self.sub_td / "wolf_runner.sh"
        runner.write_text(header + "\nPARTS_SCRIPT=\"" + str(self.parts_script) + "\"\n" + body + "\n" + footer, encoding="utf-8")
        runner.chmod(0o755)
        return runner

    def _run_wolf(self, runner: Path, *args, estimated_upload_bytes: int | None = None):
        cmd = args[0] if args else ""
        if cmd in ("pack", "stage_delta"):
            archive_name = args[1] if len(args) > 1 else ""
            if estimated_upload_bytes is None:
                part_bytes_val = getattr(self, "current_part_bytes", None)
                estimated_upload_bytes = self.stand.estimate_archive_upload_bound(
                    self.home_dir, archive_name, part_bytes=part_bytes_val
                )
            self.stand.check_and_reserve_budget(estimated_upload_bytes)

        env = self.stand.rclone_env.copy()
        gnubin = [
            "/opt/homebrew/opt/findutils/libexec/gnubin",
            "/opt/homebrew/opt/gnu-tar/libexec/gnubin",
            "/opt/homebrew/opt/coreutils/libexec/gnubin",
            "/usr/local/opt/findutils/libexec/gnubin",
            "/usr/local/opt/gnu-tar/libexec/gnubin",
            "/usr/local/opt/coreutils/libexec/gnubin",
        ]
        path_items = [str(self.td), str(self.sub_td)]
        path_items.extend([p for p in gnubin if os.path.isdir(p)])
        if ZSTD_BIN:
            path_items.append(str(Path(ZSTD_BIN).parent))
        if RCLONE_BIN:
            path_items.append(str(Path(RCLONE_BIN).parent))
        path_items.append(env.get("PATH", ""))
        env["PATH"] = ":".join(path_items)

        res = subprocess.run([str(runner), *args], capture_output=True, text=True, env=env, timeout=300)
        return res

    def _sync_drive_objects(self) -> dict:
        """Синхронизирует созданные в хранилище объекты с локальным реестром стенда."""
        return self.stand.sync_remote_objects(rclone_cmd=RCLONE_BIN)

    # -------------------------------------------------------------------------
    # Сценарий Б (уточнённый): Активная база и активная дельта из частей (parts)
    # -------------------------------------------------------------------------
    def test_scenario_b_refined_parts_base_and_delta(self):
        """Сценарий Б: База и накопительная дельта созданы настоящим pack() из частей.
        - Исходные плохо сжимаемые синтетические данные;
        - До очистки явно проверяется наличие обоих .parts и всех частей;
        - Очистка удаляет только доказанные кандидаты по registered file_id;
        - Неизвестное поколение и активные данные остаются на месте;
        - Полное сравнение словаря путей и SHA-256 после unpack().
        """
        print("\n=== [Сценарий Б Уточнённый] База и дельта из частей + кандидаты + полное сравнение SHA-256 ===")
        game_dir = self.home_dir / "sa" / "steamapps" / "common" / "DriveGameB"
        game_dir.mkdir(parents=True, exist_ok=True)

        # 1. Формирование базы (плохо сжимаемые синтетические данные)
        # Суммарный размер ~32 КБ. С WOLF_PART_BYTES=5000 база гарантированно разбивается на ~7 частей.
        data_large = make_incompressible_payload("b_large", 24000)
        data_f1 = make_incompressible_payload("b_f1", 3000)
        data_f2 = make_incompressible_payload("b_f2", 3000)
        data_f3 = make_incompressible_payload("b_f3", 2000)

        (game_dir / "base_large.dat").write_bytes(data_large)
        (game_dir / "file1.dat").write_bytes(data_f1)
        (game_dir / "file2.dat").write_bytes(data_f2)
        (game_dir / "file3.dat").write_bytes(data_f3)

        runner = self._create_real_wolf_runner(self.home_dir, part_bytes=5000)
        r_pack1 = self._run_wolf(runner, "pack", "sgame--DriveGameB")
        self.assertEqual(r_pack1.returncode, 0, f"pack base failed: {r_pack1.stderr}")

        # Синхронизация созданных объектов базы в Drive
        listing_base = self._sync_drive_objects()
        base_obj = "sgame--DriveGameB.tar.zst"
        base_parts_name = f"{base_obj}.parts"
        self.assertIn(base_parts_name, listing_base, "Боевое оглавление базы .parts отсутствует на Drive!")

        # Получаем хеш версии базы (первые 8 символов)
        base_ver = (self.s_dir / "v" / "sgame--DriveGameB").read_text(encoding="utf-8").strip()
        base_hash = base_ver[:8]
        self.assertTrue(base_hash, "Хеш базовой версии пуст!")
        delta_obj = f"{base_obj}.{base_hash}"

        # 2. Формирование накопительной дельты
        # Дельта: изменение file1.dat (3 КБ), добавление file4.dat (2 КБ), удаление file3.dat (2 КБ)
        # Общий объём дельты = 7 КБ. (7 КБ * 4 = 28 КБ < 32 КБ базы -> движок выбирает накопительную дельту)
        # При WOLF_PART_BYTES=5000 дельта ~7 КБ также разбивается на части!
        data_f1_v2 = make_incompressible_payload("b_f1_v2", 3000)
        data_f4 = make_incompressible_payload("b_f4", 2000)

        (game_dir / "file1.dat").write_bytes(data_f1_v2)
        (game_dir / "file4.dat").write_bytes(data_f4)
        (game_dir / "file3.dat").unlink()

        r_pack2 = self._run_wolf(runner, "pack", "sgame--DriveGameB")
        self.assertEqual(r_pack2.returncode, 0, f"pack delta failed: {r_pack2.stderr}")

        # Синхронизация созданных объектов дельты в Drive
        listing_delta = self._sync_drive_objects()
        delta_parts_name = f"{delta_obj}.parts"
        self.assertIn(delta_parts_name, listing_delta, f"Боевое оглавление дельты {delta_parts_name} отсутствует на Drive!")

        # 3. Добавление кандидатов вытесненного поколения и неизвестного поколения (PROTECTED)
        old_part1 = f"{base_obj}.deadbeef.g11111111-0001"
        old_part2 = f"{base_obj}.deadbeef.g11111111-0002"
        unk_part = f"{base_obj}.g99999999-0001"

        cand1_obj = self.stand.upload_synthetic_file(old_part1, make_incompressible_payload("cand1", 3500))
        cand2_obj = self.stand.upload_synthetic_file(old_part2, make_incompressible_payload("cand2", 3500))
        unk_obj = self.stand.upload_synthetic_file(unk_part, make_incompressible_payload("unk", 3500))

        # 4. Явная проверка до очистки:
        # Наличие обоих боевых .parts и всех перечисленных в них частей
        base_idx_bytes = subprocess.run([RCLONE_BIN, "cat", f"{self.stand.remote_name}{base_parts_name}"],
                                        capture_output=True, env=self.stand.rclone_env, timeout=15).stdout
        delta_idx_bytes = subprocess.run([RCLONE_BIN, "cat", f"{self.stand.remote_name}{delta_parts_name}"],
                                         capture_output=True, env=self.stand.rclone_env, timeout=15).stdout

        _, _, base_parts_list = parse_parts_index_text(base_idx_bytes.decode("utf-8"), base_obj)
        _, _, delta_parts_list = parse_parts_index_text(delta_idx_bytes.decode("utf-8"), delta_obj)

        current_remote_names = set(self._sync_drive_objects().keys())
        for pn, _, _ in base_parts_list:
            self.assertIn(pn, current_remote_names, f"Часть базы {pn} отсутствует на Drive до очистки!")
        for pn, _, _ in delta_parts_list:
            self.assertIn(pn, current_remote_names, f"Часть дельты {pn} отсутствует на Drive до очистки!")

        # 5. Предварительный просмотр (Preview)
        preview_report = classify_archive(
            remote=self.stand.remote_name,
            obj_name="sgame--DriveGameB",
            grant_file=str(self.grant_file),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            rclone_cmd=RCLONE_BIN,
        )
        self.assertTrue(preview_report.get("cleanup_allowed"), f"Очистка заблокирована: {preview_report}")
        report_files = {f["name"]: f["category"] for f in preview_report.get("files", [])}

        self.assertEqual(report_files.get(base_parts_name), "ACTIVE")
        self.assertEqual(report_files.get(delta_parts_name), "ACTIVE")
        for pn, _, _ in base_parts_list:
            self.assertEqual(report_files.get(pn), "ACTIVE")
        for pn, _, _ in delta_parts_list:
            self.assertEqual(report_files.get(pn), "ACTIVE")
        self.assertEqual(report_files.get(old_part1), "CANDIDATE")
        self.assertEqual(report_files.get(old_part2), "CANDIDATE")
        self.assertEqual(report_files.get(unk_part), "PROTECTED")

        # Сохранение артефакта preview
        self.stand.save_evidence("scenario_b_preview.json", preview_report)

        # 6. Исполнение очистки
        clean_res = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameB",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(clean_res["status"], "completed")
        for d in clean_res.get("deleted", []):
            self.stand.record_candidate_deletion(d.get("size", 0))

        # Фиксация удалённых файлов
        deletions_evidence = {
            "status": clean_res["status"],
            "expected_deletions": [
                {"name": old_part1, "id": cand1_obj["id"]},
                {"name": old_part2, "id": cand2_obj["id"]},
            ],
            "actual_deletions": clean_res.get("deleted", []),
            "remaining_candidates": clean_res.get("remaining", []),
            "deleted_ids_matched": {d["name"]: d.get("id") == self.stand.registry[d["name"]]["id"] for d in clean_res.get("deleted", [])},
        }
        self.stand.save_evidence("scenario_b_deletions.json", deletions_evidence)

        # 7. Проверка состояния Drive после очистки
        drive_after_listing = _get_remote_listing(self.stand.remote_name, rclone_cmd=RCLONE_BIN)
        self.stand.save_evidence("scenario_b_drive_listing_after.json", drive_after_listing)

        self.assertTrue(self.stand.adapter.verify_deleted(old_part1, cand1_obj))
        self.assertTrue(self.stand.adapter.verify_deleted(old_part2, cand2_obj))
        self.assertFalse(self.stand.adapter.verify_deleted(unk_part, unk_obj))

        after_names = set(it["Name"] for it in drive_after_listing)
        self.assertIn(base_parts_name, after_names)
        self.assertIn(delta_parts_name, after_names)
        self.assertIn(unk_part, after_names)
        for pn, _, _ in base_parts_list:
            self.assertIn(pn, after_names)
        for pn, _, _ in delta_parts_list:
            self.assertIn(pn, after_names)

        # 8. Полное восстановление через unpack() и сравнение дерева хешей
        shutil.rmtree(game_dir)
        r_unpack = self._run_wolf(runner, "unpack", "sgame--DriveGameB")
        self.assertEqual(r_unpack.returncode, 0, f"unpack failed: {r_unpack.stderr}")

        actual_tree = get_dir_tree_hashes(game_dir)
        expected_tree = {
            "base_large.dat": sha256_bytes(data_large),
            "file1.dat": sha256_bytes(data_f1_v2),
            "file2.dat": sha256_bytes(data_f2),
            "file4.dat": sha256_bytes(data_f4),
        }
        self.assertEqual(actual_tree, expected_tree, f"Дерево файлов не совпало 1:1!\nActual: {actual_tree}\nExpected: {expected_tree}")

        restore_evidence = {
            "unpack_returncode": r_unpack.returncode,
            "expected_tree": expected_tree,
            "actual_tree": actual_tree,
            "trees_match": actual_tree == expected_tree,
            "verified_files_count": len(actual_tree),
        }
        self.stand.save_evidence("scenario_b_restore_verification.json", restore_evidence)
        print("[Сценарий Б Уточнённый] УСПЕШНО: активные база и дельта из частей сохранены, кандидаты удалены по ID, дерево 100% сошлось.")

    # -------------------------------------------------------------------------
    # Сценарий В (уточнённый): Незавершённая попытка дельты через parts stage
    # -------------------------------------------------------------------------
    def test_scenario_c_refined_delta_stage_attempts(self):
        """Сценарий В:
        - Незавершённая попытка дельты создана настоящим parts stage;
        - Раздельная проверка: только attempt (без pending) -> PROTECTED, 0 удалений;
        - Раздельная проверка: только pending (без attempt) -> PROTECTED, 0 удалений;
        - Завершение публикации штатным resume;
        - Полное восстановление через unpack и сравнение всего дерева SHA-256.
        """
        print("\n=== [Сценарий В Уточнённый] Незавершённая попытка дельты + раздельная проверка attempt/pending + resume + unpack ===")
        game_dir = self.home_dir / "sa" / "steamapps" / "common" / "DriveGameC"
        game_dir.mkdir(parents=True, exist_ok=True)

        # 1. Формирование базы (плохо сжимаемые данные)
        data_c1 = make_incompressible_payload("c1", 15000)
        data_c2 = make_incompressible_payload("c2", 15000)
        (game_dir / "c_base1.dat").write_bytes(data_c1)
        (game_dir / "c_base2.dat").write_bytes(data_c2)

        runner = self._create_real_wolf_runner(self.home_dir, part_bytes=5000)
        r_pack = self._run_wolf(runner, "pack", "sgame--DriveGameC")
        self.assertEqual(r_pack.returncode, 0, f"pack scenario C base failed: {r_pack.stderr}")

        # Синхронизация созданных объектов базы в Drive
        listing_base = self._sync_drive_objects()
        base_obj = "sgame--DriveGameC.tar.zst"
        base_ver = (self.s_dir / "v" / "sgame--DriveGameC").read_text(encoding="utf-8").strip()
        base_hash = base_ver[:8]
        self.assertTrue(base_hash, "Хеш базовой версии C пуст!")
        delta_obj = f"{base_obj}.{base_hash}"

        # 2. Подготовка незавершённой попытки дельты через настоящий parts stage
        data_c2_v2 = make_incompressible_payload("c2_v2", 8000)
        data_c_delta = make_incompressible_payload("c_delta", 4000)
        (game_dir / "c_base2.dat").write_bytes(data_c2_v2)
        (game_dir / "c_delta_new.dat").write_bytes(data_c_delta)

        # Вызов stage_delta в runner: создаёт .parts.new и части дельты в Drive, возвращает квитанцию
        r_stage = self._run_wolf(runner, "stage_delta", "sgame--DriveGameC", delta_obj)
        self.assertEqual(r_stage.returncode, 0, f"stage_delta failed: {r_stage.stderr}")
        raw_receipt = r_stage.stdout.strip()
        self.assertTrue(raw_receipt, "Квитанция stage_delta пуста!")

        # Синхронизация созданных объектов попытки в Drive
        listing_staged = self._sync_drive_objects()
        delta_staged_idx = f"{delta_obj}.parts.new"
        self.assertIn(delta_staged_idx, listing_staged, f"Оглавление попытки {delta_staged_idx} отсутствует на Drive!")

        # 3. Проверка 1: Защита ТОЛЬКО при наличии attempt (без pending)
        pending_path = self.s_dir / "pending" / "sgame--DriveGameC"
        if pending_path.exists():
            pending_path.unlink()

        att_path = self.t_dir / f"attempt-{delta_obj.replace('/', '_')}"
        att_path.write_text(f"unknown\n{raw_receipt}\n", encoding="utf-8")

        preview1 = classify_archive(
            remote=self.stand.remote_name,
            obj_name="sgame--DriveGameC",
            grant_file=str(self.grant_file),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            rclone_cmd=RCLONE_BIN,
        )
        self.stand.save_evidence("scenario_c_attempt_preview.json", preview1)
        report1_files = {f["name"]: f["category"] for f in preview1.get("files", [])}
        self.assertEqual(report1_files.get(delta_staged_idx), "PROTECTED")

        clean1 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameC",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(clean1["status"], "noop")
        self.assertEqual(len(clean1["deleted"]), 0)

        # 4. Проверка 2: Защита ТОЛЬКО при наличии pending (без attempt)
        if att_path.exists():
            att_path.unlink()

        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(f"object {delta_obj}\n{raw_receipt}\n", encoding="utf-8")

        preview2 = classify_archive(
            remote=self.stand.remote_name,
            obj_name="sgame--DriveGameC",
            grant_file=str(self.grant_file),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            rclone_cmd=RCLONE_BIN,
        )
        self.stand.save_evidence("scenario_c_pending_preview.json", preview2)
        report2_files = {f["name"]: f["category"] for f in preview2.get("files", [])}
        self.assertEqual(report2_files.get(delta_staged_idx), "PROTECTED")

        clean2 = execute_stand_cleanup(
            stand_dir=self.stand.remote_name,
            obj_name="sgame--DriveGameC",
            grant_file=str(self.grant_file),
            lock_dir=str(self.l_dir),
            tmp_dir=str(self.t_dir),
            state_dir=str(self.s_dir),
            run_id_val=self.stand.run_id,
            iid_val="999001",
            folder_id_val=self.stand.folder_id,
            drive_test_mode=True,
            drive_test_folder_id=self.stand.folder_id,
            drive_test_registry=self.stand.registry,
            rclone_cmd=RCLONE_BIN,
            drive_adapter=self.stand.adapter,
        )
        self.assertEqual(clean2["status"], "noop")
        self.assertEqual(len(clean2["deleted"]), 0)

        deletions_c = {
            "attempt_only_status": clean1["status"],
            "attempt_only_deleted": clean1["deleted"],
            "pending_only_status": clean2["status"],
            "pending_only_deleted": clean2["deleted"],
            "total_deleted_count": 0,
        }
        self.stand.save_evidence("scenario_c_deletions.json", deletions_c)

        # 5. Штатный resume настоящим wolf-parts.py
        env = self.stand.rclone_env.copy()
        env["WOLF_TMP"] = str(self.t_dir)
        path_items = [str(self.td), str(self.sub_td)]
        for p in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/homebrew/opt/gnu-tar/libexec/gnubin", "/opt/homebrew/opt/coreutils/libexec/gnubin"):
            if os.path.isdir(p):
                path_items.append(p)
        if RCLONE_BIN:
            path_items.append(str(Path(RCLONE_BIN).parent))
        if ZSTD_BIN:
            path_items.append(str(Path(ZSTD_BIN).parent))
        path_items.append(env.get("PATH", ""))
        env["PATH"] = ":".join(path_items)

        r_resume = subprocess.run(
            [sys.executable, str(self.parts_script), "resume", self.stand.remote_name, delta_obj],
            input=raw_receipt.encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(r_resume.returncode, 0, f"resume failed: {r_resume.stderr.decode('utf-8', 'replace')}")

        # Проверка Drive: .parts.new стал боевым .parts
        listing_after_resume = self._sync_drive_objects()
        self.stand.save_evidence("scenario_c_drive_listing_after.json", list(listing_after_resume.values()))
        delta_active_idx = f"{delta_obj}.parts"
        self.assertIn(delta_active_idx, listing_after_resume)

        # 6. Полное восстановление через unpack() и сравнение дерева SHA-256
        shutil.rmtree(game_dir)
        r_unpack = self._run_wolf(runner, "unpack", "sgame--DriveGameC")
        self.assertEqual(r_unpack.returncode, 0, f"unpack failed: {r_unpack.stderr}")

        actual_tree = get_dir_tree_hashes(game_dir)
        expected_tree = {
            "c_base1.dat": sha256_bytes(data_c1),
            "c_base2.dat": sha256_bytes(data_c2_v2),
            "c_delta_new.dat": sha256_bytes(data_c_delta),
        }
        self.assertEqual(actual_tree, expected_tree, f"Дерево файлов Сценария В не совпало 1:1!\nActual: {actual_tree}\nExpected: {expected_tree}")

        restore_evidence = {
            "resume_returncode": r_resume.returncode,
            "unpack_returncode": r_unpack.returncode,
            "expected_tree": expected_tree,
            "actual_tree": actual_tree,
            "trees_match": actual_tree == expected_tree,
            "verified_files_count": len(actual_tree),
        }
        self.stand.save_evidence("scenario_c_restore_verification.json", restore_evidence)
        print("[Сценарий В Уточнённый] УСПЕШНО: попытка защищена отдельно в attempt и pending (0 удалений), resume завершён, unpack 100% сошёлся.")



class TestDriveStandAccountingAndErrorHandling(unittest.TestCase):
    """Локальные модульные проверки строгого учёта объёмов и обработки сбоев чтения объектов."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="stand_accounting_test_"))
        self.stand = DriveStandManager(local_mode=True, local_root_dir=self.td)
        self.home_dir = self.td / "home_user"
        self.home_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _run_wolf(self, runner: Path, *args, estimated_upload_bytes: int | None = None):
        cmd = args[0] if args else ""
        if cmd in ("pack", "stage_delta"):
            archive_name = args[1] if len(args) > 1 else ""
            if estimated_upload_bytes is None:
                estimated_upload_bytes = self.stand.estimate_archive_upload_bound(self.home_dir, archive_name)
            self.stand.check_and_reserve_budget(estimated_upload_bytes)
        env = self.stand.rclone_env.copy()
        return subprocess.run([str(runner), *args], capture_output=True, text=True, env=env)

    def test_budget_check_before_transfer_rejects_unbounded_and_exceeded(self):
        # 1. Отказ при неопределённом размере (None)
        with self.assertRaises(RuntimeError) as cm_none:
            self.stand.check_budget_before_transfer(None)
        self.assertIn("размер передаваемых данных не определён", str(cm_none.exception))

        # 2. Отказ при отрицательном размере
        with self.assertRaises(RuntimeError) as cm_neg:
            self.stand.check_budget_before_transfer(-100)
        self.assertIn("размер передаваемых данных не определён", str(cm_neg.exception))

        # 3. Отказ при превышении лимита хранилища
        overflow = self.stand.MAX_UPLOAD_BYTES + 1
        with self.assertRaises(RuntimeError) as cm_ovf:
            self.stand.check_budget_before_transfer(overflow)
        self.assertIn("превышение бюджета хранилища стенда", str(cm_ovf.exception))

        # 4. Допустимый объём проходит и накапливается
        self.stand.check_budget_before_transfer(1024)
        self.assertEqual(self.stand.cumulative_admitted_bytes, 1024)

    @patch(f"{__name__}.subprocess.Popen")
    def test_upload_synthetic_file_path_blocks_overflow_and_unknown_and_runs_allowed(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        # 1. Неизвестный размер (data is None) блокирует запуск загрузки
        with self.assertRaises(RuntimeError) as cm_none:
            self.stand.upload_synthetic_file("test_none.dat", None)
        self.assertIn("размер передаваемых данных не определён", str(cm_none.exception))
        mock_popen.assert_not_called()

        # 2. Превышение лимита блокирует запуск загрузки
        overflow_size = self.stand.MAX_UPLOAD_BYTES + 10
        with self.assertRaises(RuntimeError) as cm_ovf:
            self.stand.upload_synthetic_file("test_ovf.dat", b"x" * overflow_size)
        self.assertIn("превышение бюджета хранилища стенда", str(cm_ovf.exception))
        mock_popen.assert_not_called()

        # 3. Разрешённый объём запускается
        valid_data = b"hello_stand_data"
        self.stand.upload_synthetic_file("test_ok.dat", valid_data)
        mock_popen.assert_called_once()
        self.assertEqual(self.stand.cumulative_admitted_bytes, len(valid_data))

    @patch(f"{__name__}.subprocess.run")
    def test_pack_upload_path_blocks_overflow_and_unknown_and_runs_allowed(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=["runner", "pack"], returncode=0, stdout="", stderr="")

        fake_runner = self.td / "mock_runner.sh"
        fake_runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_runner.chmod(0o755)

        # 1. Неизвестный размер (каталог игры не существует) блокирует вызов pack
        with self.assertRaises(RuntimeError) as cm_unk:
            self._run_wolf(fake_runner, "pack", "sgame--NonExistentGame")
        self.assertIn("размер передаваемых данных не определён", str(cm_unk.exception))
        mock_run.assert_not_called()

        # 2. Превышение лимита каталогом игры блокирует вызов pack
        huge_game_dir = self.home_dir / "sa" / "steamapps" / "common" / "HugeGame"
        huge_game_dir.mkdir(parents=True, exist_ok=True)
        # Создаём фиктивный файл размером > MAX_UPLOAD_BYTES (через ftruncate без реального диска)
        with open(huge_game_dir / "huge.dat", "wb") as f:
            f.truncate(self.stand.MAX_UPLOAD_BYTES + 1024)

        with self.assertRaises(RuntimeError) as cm_ovf:
            self._run_wolf(fake_runner, "pack", "sgame--HugeGame")
        self.assertIn("превышение бюджета хранилища стенда", str(cm_ovf.exception))
        mock_run.assert_not_called()

        # 3. Разрешённый размер запускает pack
        small_game_dir = self.home_dir / "sa" / "steamapps" / "common" / "SmallGame"
        small_game_dir.mkdir(parents=True, exist_ok=True)
        (small_game_dir / "small.dat").write_bytes(b"small_game_bytes" * 50)

        bound_ok = self.stand.estimate_archive_upload_bound(self.home_dir, "sgame--SmallGame")
        self.assertIsNotNone(bound_ok)
        self.assertGreater(bound_ok, 0)
        self._run_wolf(fake_runner, "pack", "sgame--SmallGame")
        mock_run.assert_called_once()
        self.assertGreaterEqual(self.stand.cumulative_admitted_bytes, bound_ok)

        # 4. stage_delta через _run_wolf также использует предварительный допуск
        mock_run.reset_mock()
        self._run_wolf(fake_runner, "stage_delta", "sgame--SmallGame", "sgame--SmallGame.delta")
        mock_run.assert_called_once()

    @patch(f"{__name__}.subprocess.run")
    def test_parts_stage_path_blocks_overflow_and_unknown_and_runs_allowed(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=["python", "parts", "stage"], returncode=0, stdout="", stderr="")

        parts_script = self.td / "wolf-parts.py"
        parts_script.write_text("# parts\n", encoding="utf-8")

        # 1. Неизвестный размер (input_data is None) блокирует запуск stage
        with self.assertRaises(RuntimeError) as cm_none:
            self.stand.run_parts_stage(parts_script, "delta.obj", None)
        self.assertIn("размер передаваемых данных не определён", str(cm_none.exception))
        mock_run.assert_not_called()

        # 2. Превышение лимита блокирует запуск stage
        with self.assertRaises(RuntimeError) as cm_ovf:
            self.stand.run_parts_stage(parts_script, "delta.obj", b"data", estimated_bytes=self.stand.MAX_UPLOAD_BYTES + 500)
        self.assertIn("превышение бюджета хранилища стенда", str(cm_ovf.exception))
        mock_run.assert_not_called()

        # 3. Разрешённый размер запускает stage
        self.stand.run_parts_stage(parts_script, "delta.obj", b"valid_delta_bytes")
        mock_run.assert_called_once()

    @patch(f"{__name__}.subprocess.Popen")
    def test_sequential_operations_and_retries_consume_budget_without_free_retry(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        # Задаём тестовый небольшой лимит 100 000 байт
        self.stand.MAX_UPLOAD_BYTES = 100_000

        # Операция 1: загрузка 40 000 байт
        data1 = b"a" * 40_000
        self.stand.upload_synthetic_file("part1.dat", data1)
        self.assertEqual(self.stand.cumulative_admitted_bytes, 40_000)
        self.assertEqual(mock_popen.call_count, 1)

        # Операция 2: повторная загрузка (retry) того же файла или новой попытки того же размера 40 000 байт
        # Повтор НЕ получает бюджет бесплатно, а увеличивает cumulative_admitted_bytes
        data_retry = b"a" * 40_000
        self.stand.upload_synthetic_file("part1.dat", data_retry)
        self.assertEqual(self.stand.cumulative_admitted_bytes, 80_000)
        self.assertEqual(mock_popen.call_count, 2)

        # Операция 3: последовательная загрузка ещё 30 000 байт (80 000 + 30 000 = 110 000 > 100 000)
        # Должна быть заблокирована ДО запуска передачи
        data3 = b"b" * 30_000
        with self.assertRaises(RuntimeError) as cm_ovf:
            self.stand.upload_synthetic_file("part2.dat", data3)
        self.assertIn("превышение бюджета хранилища стенда", str(cm_ovf.exception))
        # mock_popen не должен вызываться в 3-й раз!
        self.assertEqual(mock_popen.call_count, 2)

    def test_accounting_metrics_separation(self):
        # Исходное состояние
        self.assertEqual(self.stand.cumulative_admitted_bytes, 0)
        self.assertEqual(self.stand.registered_objects_size_sum, 0)
        self.assertEqual(self.stand.deleted_bytes_candidates, 0)
        self.assertEqual(self.stand.remaining_objects_size_sum, 0)

        # Резервирование и регистрация первого объекта (100 байт)
        self.stand.check_and_reserve_budget(100)
        obj1 = self.stand.register_created_object("obj1.dat", b"a" * 100, file_id="file_id_1")
        self.assertEqual(self.stand.cumulative_admitted_bytes, 100)
        self.assertEqual(self.stand.registered_objects_size_sum, 100)
        self.assertEqual(self.stand.deleted_bytes_candidates, 0)
        self.assertEqual(self.stand.remaining_objects_size_sum, 100)

        # Резервирование и регистрация второго объекта (250 байт)
        self.stand.check_and_reserve_budget(250)
        obj2 = self.stand.register_created_object("obj2.dat", b"b" * 250, file_id="file_id_2")
        self.assertEqual(self.stand.cumulative_admitted_bytes, 350)
        self.assertEqual(self.stand.registered_objects_size_sum, 350)
        self.assertEqual(self.stand.deleted_bytes_candidates, 0)
        self.assertEqual(self.stand.remaining_objects_size_sum, 350)

        # Фиксация удаления кандидата obj1 (100 байт)
        self.stand.record_candidate_deletion(100)
        self.assertEqual(self.stand.cumulative_admitted_bytes, 350)
        self.assertEqual(self.stand.registered_objects_size_sum, 350)
        self.assertEqual(self.stand.deleted_bytes_candidates, 100)
        self.assertEqual(self.stand.remaining_objects_size_sum, 250)

        # Проверка структуры сохранённого manifest.json
        manifest_data = json.loads(self.stand.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["cumulative_admitted_bytes"], 350)
        self.assertEqual(manifest_data["registered_objects_size_sum"], 350)
        self.assertEqual(manifest_data["deleted_bytes_candidates"], 100)
        self.assertEqual(manifest_data["remaining_objects_size_sum"], 250)
        self.assertIsNone(manifest_data["network_uploaded_bytes"])
        self.assertTrue(manifest_data["network_measurement_status"].startswith("unmeasured"))
        self.assertNotIn("uploaded_bytes_network", manifest_data)

        # Запрет регистрации фиктивных данных (None вместо байтов)
        with self.assertRaises(RuntimeError) as cm_fake:
            self.stand.register_created_object("fake.dat", None, file_id="fake_id")
        self.assertIn("регистрация фиктивных данных запрещена", str(cm_fake.exception))

    @patch(f"{__name__}.subprocess.run")
    @patch(f"{__name__}._get_remote_listing")
    def test_rclone_cat_failure_raises_explicit_error_and_does_not_register(self, mock_listing, mock_run):
        mock_listing.return_value = [{"Name": "failing_object.tar.zst", "Size": 1024, "ID": "id_fail"}]
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rclone", "cat"],
            returncode=1,
            stdout=b"",
            stderr=b"rclone: 404 Not Found",
        )

        with self.assertRaises(RuntimeError) as cm:
            self.stand.sync_remote_objects()

        self.assertIn("Ошибка чтения объекта failing_object.tar.zst из хранилища", str(cm.exception))
        self.assertIn("регистрация фиктивного содержимого запрещена", str(cm.exception))
        self.assertNotIn("failing_object.tar.zst", self.stand.registry)

    @patch(f"{__name__}.subprocess.run")
    @patch(f"{__name__}._get_remote_listing")
    def test_read_size_mismatch_raises_explicit_error_and_does_not_register(self, mock_listing, mock_run):
        mock_listing.return_value = [{"Name": "mismatched.dat", "Size": 1024, "ID": "id_mism"}]
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rclone", "cat"],
            returncode=0,
            stdout=b"x" * 100,
            stderr=b"",
        )

        with self.assertRaises(RuntimeError) as cm:
            self.stand.sync_remote_objects()

        self.assertIn("Размер объекта mismatched.dat изменился во время чтения", str(cm.exception))
        self.assertIn("Отказ от регистрации по неполным данным", str(cm.exception))
        self.assertNotIn("mismatched.dat", self.stand.registry)

    @patch(f"{__name__}.subprocess.run")
    @patch(f"{__name__}._get_remote_listing")
    def test_sync_verifies_budget_before_registration(self, mock_listing, mock_run):
        huge_size = self.stand.MAX_UPLOAD_BYTES + 5000
        mock_listing.return_value = [{"Name": "huge.dat", "Size": huge_size, "ID": "id_huge"}]
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rclone", "cat"],
            returncode=0,
            stdout=b"h" * huge_size,
            stderr=b"",
        )

        with self.assertRaises(RuntimeError) as cm:
            self.stand.sync_remote_objects()

        self.assertIn("превышение бюджета хранилища стенда", str(cm.exception))
        self.assertNotIn("huge.dat", self.stand.registry)

    def test_estimate_archive_upload_bound_conservative_guarantee(self):
        """Проверяет строгое консервативное свойство (estimate >= actual) для всех трудных случаев:
        1. Много маленьких файлов (выравнивание tar на границу 512 байт);
        2. Длинные пути > 100 символов (заголовки и блоки GNU LongLink);
        3. Несжимаемые случайные данные (худший случай zstd);
        4. Дробление на множество частей (оглавление .parts / .parts.new).
        """
        # --- Случай 1: Много маленьких файлов с выравниванием tar ---
        small_dir = self.home_dir / "sa" / "steamapps" / "common" / "SmallFilesGame"
        small_dir.mkdir(parents=True, exist_ok=True)
        for i in range(100):
            (small_dir / f"f_{i:03d}.dat").write_bytes(f"{i}".encode())

        bound_small = self.stand.estimate_archive_upload_bound(self.home_dir, "sgame--SmallFilesGame")
        self.assertIsNotNone(bound_small)

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            tar.add(str(small_dir), arcname="sa/steamapps/common/SmallFilesGame")
        tar_bytes = tar_buf.getvalue()
        compressed_small = compress_zstd(tar_bytes)
        actual_small_size = len(compressed_small)

        self.assertGreaterEqual(
            bound_small, actual_small_size,
            f"Оценка {bound_small} оказалась меньше фактического размера {actual_small_size}!"
        )

        # --- Случай 2: Длинные пути (> 100 символов, GNU LongLink) ---
        long_dir = self.home_dir / "sa" / "steamapps" / "common" / "LongPathsGame"
        sub_nested = long_dir / ("very_long_nested_directory_level_one_name_exceeding_standard_limits" * 2)
        sub_nested.mkdir(parents=True, exist_ok=True)
        (sub_nested / ("very_long_file_name_that_definitely_requires_gnu_longlink_header_block_" * 2 + ".bin")).write_bytes(b"long_path_content")

        bound_long = self.stand.estimate_archive_upload_bound(self.home_dir, "sgame--LongPathsGame")
        self.assertIsNotNone(bound_long)

        tar_buf_long = io.BytesIO()
        with tarfile.open(fileobj=tar_buf_long, mode="w", format=tarfile.GNU_FORMAT) as tar:
            tar.add(str(long_dir), arcname="sa/steamapps/common/LongPathsGame")
        actual_long_size = len(compress_zstd(tar_buf_long.getvalue()))

        self.assertGreaterEqual(
            bound_long, actual_long_size,
            f"Оценка длинных путей {bound_long} оказалась меньше фактического размера {actual_long_size}!"
        )

        # --- Случай 3: Несжимаемые случайные данные (худший случай zstd) ---
        rnd_dir = self.home_dir / "sa" / "steamapps" / "common" / "RandomDataGame"
        rnd_dir.mkdir(parents=True, exist_ok=True)
        random_payload = os.urandom(50_000)
        (rnd_dir / "random.dat").write_bytes(random_payload)

        bound_rnd = self.stand.estimate_archive_upload_bound(self.home_dir, "sgame--RandomDataGame")
        self.assertIsNotNone(bound_rnd)

        tar_buf_rnd = io.BytesIO()
        with tarfile.open(fileobj=tar_buf_rnd, mode="w") as tar:
            tar.add(str(rnd_dir), arcname="sa/steamapps/common/RandomDataGame")
        actual_rnd_size = len(compress_zstd(tar_buf_rnd.getvalue()))

        self.assertGreaterEqual(
            bound_rnd, actual_rnd_size,
            f"Оценка несжимаемых данных {bound_rnd} оказалась меньше фактического размера {actual_rnd_size}!"
        )

        # --- Случай 4: Дробление на множество частей (25+ частей) и оглавление ---
        bound_parts = self.stand.estimate_archive_upload_bound(
            self.home_dir, "sgame--RandomDataGame", part_bytes=1000
        )
        self.assertIsNotNone(bound_parts)

        part_chunk_size = 1000
        zstd_data = compress_zstd(tar_buf_rnd.getvalue())
        num_parts = (len(zstd_data) + part_chunk_size - 1) // part_chunk_size
        fake_index = f"vastgame-parts 1\n{len(zstd_data)}\n" + "".join(
            f"sgame--RandomDataGame.hash.g00000001-{i:04d}\t{'0'*32}\t{part_chunk_size}\n"
            for i in range(num_parts)
        )
        actual_parts_and_index_total = len(zstd_data) + len(fake_index.encode("utf-8"))

        self.assertGreaterEqual(
            bound_parts, actual_parts_and_index_total,
            f"Оценка с частями {bound_parts} оказалась меньше фактического размера {actual_parts_and_index_total}!"
        )


if __name__ == "__main__":
    unittest.main()


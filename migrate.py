# migrate.py — перенести архивы из старой папки vastai-cloud-games в папку приложения.
# Запуск: vastgame migrate
#
# Зачем: со своим OAuth-клиентом (scope drive.file) приложение видит только файлы, которые создало
# само. Архивы, созданные раньше через общий клиент rclone, ему не видны. Перенос читает их старым
# токеном (полный доступ) и записывает новым — после этого приложение и машины считают их своими.
# Файлы идут через этот компьютер: скачать → загрузить. Старая папка не меняется — она остаётся копией.

import json
import sys

import keyring

import drive
from vastapi import SERVICE, list_instances

FOLDER = drive.FOLDER


def ask(question):
    try:
        import readline  # noqa: F401 — стрелки и Backspace в ответе (на Windows модуля нет)
    except ImportError:
        pass
    return input(f"{question} [да/нет]: ").strip().lower() in ("да", "yes", "y", "д")


def lsjson(remote_path, *flags):
    r = drive.rclone_both("lsjson", *flags, remote_path, timeout=600)
    return json.loads(r.stdout or "[]") if r.returncode == 0 else None


# ---- 0. Условия
if drive.is_old_token():
    sys.exit("Сначала войди в Google через клиент приложения: vastgame setup (шаг 2).")
if not keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN_OLD"):
    sys.exit("Нет прежнего входа в Google (общий клиент rclone) — читать старые архивы нечем.")
if list_instances():
    sys.exit("Сейчас арендована машина — сначала vastgame stop: иначе она может писать в папку во время переноса.")

# ---- 1. Куда: папка приложения. Её ID — чтобы отличить от старых папок с тем же именем
new_root = lsjson("vgdrive:", "--dirs-only") or []
new_dir = next((d for d in new_root if d["Name"] == FOLDER), None)
new_files = lsjson(f"vgdrive:{FOLDER}", "--files-only") if new_dir else []
if new_files:
    print(f"В папке приложения уже есть файлы ({len(new_files)}) — машины уже работали с новым входом.")
    if not ask("Всё равно переносить? Файлы с теми же именами будут заменены старыми"):
        sys.exit("Отменено.")

# ---- 2. Откуда: все папки vastai-cloud-games на Drive (кроме папки приложения)
print("Ищу папки vastai-cloud-games на Google Drive (старым входом)...")
found = [d for d in (lsjson("vgold:", "-R", "--dirs-only", "--max-depth", "3") or [])
         if d["Name"] == FOLDER and (not new_dir or d.get("ID") != new_dir.get("ID"))]
if not found:
    sys.exit("Старых папок vastai-cloud-games не найдено (искал до 3 уровней вложенности).")

rows = []
for d in found:
    files = lsjson("vgold:" + d["Path"], "--files-only") or []
    rows.append((d, files, sum(f.get("Size", 0) for f in files) / 1e9,
                 max((f.get("ModTime", "")[:16] for f in files), default="—")))
print()
for n, (d, files, gb, last) in enumerate(rows, 1):
    names = sorted({f["Name"].split(".tar.zst")[0] for f in files})
    print(f"  {n}. {d['Path']}")
    print(f"     {len(files)} файлов, {gb:.1f} ГБ, последнее изменение {last}")
    print(f"     {', '.join(names[:8])}{' …' if len(names) > 8 else ''}")
choice = input(f"\nИз какой переносить? Номер (1–{len(rows)}), Enter — отмена: ").strip()
if not choice.isdigit() or not 1 <= int(choice) <= len(rows):
    sys.exit("Отменено.")
src, files, gb, _ = rows[int(choice) - 1]

print(f"\nПеренесу {len(files)} файлов ({gb:.1f} ГБ) из «{src['Path']}» в папку приложения «{FOLDER}».")
print("Старая папка останется как есть — это резервная копия.")
if not ask("Переносить?"):
    sys.exit("Отменено.")

# ---- 3. Копирование: через этот компьютер, с прогрессом на экране
r = drive.rclone_both("copy", "vgold:" + src["Path"], f"vgdrive:{FOLDER}",
                      "--progress", "--stats-one-line", "--transfers", "4", capture=False)
if r.returncode != 0:
    sys.exit(f"\n!!! Копирование завершилось с ошибкой (код {r.returncode}). Запусти vastgame migrate ещё раз —\n"
             "уже скопированные файлы rclone пропустит.")

# ---- 4. Сверка: те же имена, размеры и контрольные суммы MD5 (Drive считает их сам)
print("\nСверяю контрольные суммы...")
r = drive.rclone_both("check", "vgold:" + src["Path"], f"vgdrive:{FOLDER}", "--one-way", timeout=600)
if r.returncode != 0:
    print("\n".join(line for line in (r.stderr or "").splitlines() if "ERROR" in line)[-1500:])
    sys.exit("!!! Сверка не прошла — не удаляй старую папку. Запусти vastgame migrate ещё раз.")
print(f"✓ Перенесено и сверено: {len(files)} файлов, {gb:.1f} ГБ. Машины теперь восстановят всё отсюда.")

# ---- 5. Прочие старые папки vastai-cloud-games (например, от тестов) — предложить убрать в корзину
others = [d for d, *_ in rows if d.get("ID") != src.get("ID")]
for d in others:
    print(f"\nЕсть ещё старая папка «{d['Path']}» — она больше не нужна приложению.")
    if ask("Убрать её в корзину Google Drive (30 дней можно вернуть)?"):
        r = drive.rclone_both("purge", "--drive-use-trash=true", "vgold:" + d["Path"], timeout=600)
        print("  ✓ В корзине." if r.returncode == 0 else "  ✗ Не получилось — убери вручную на сайте Drive.")
print(f"\nСтарую папку «{src['Path']}» можно удалить сам, когда убедишься, что всё работает.")

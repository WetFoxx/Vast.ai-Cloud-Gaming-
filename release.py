# release.py — выпуск новой версии vastgame. Запускает Алексей на своём Mac; в приложение не входит.
#
#   .venv/bin/python release.py keygen            создать ключ подписи выпусков (один раз и навсегда)
#   .venv/bin/python release.py restore ФАЙЛ      вернуть ключ из резервной копии (например, на новом Mac)
#   .venv/bin/python release.py manifest ФАЙЛ     подписать описание для архива Mac или установщика Windows
#   .venv/bin/python release.py publish --notes "Что нового"   выпустить версию из version.py (см. publish)
#
# Windows собирается в Windows (build-win.ps1); установщик VastGame-Setup-<версия>.exe кладётся в dist.noindex/
# на этом Mac — тогда publish выпускает и его (подпись — тем же ключом здесь). Нет — выпуск только для Mac.
#
# Ключ подписи — пара Ed25519. Открытый вшит в приложение (updater.PUBLIC_KEY): обновление с другой подписью
# приложение не поставит. Закрытый — только в Связке ключей этого Mac, запись «vastgame-release», которую без
# подтверждения не читает ни одна программа, даже security (в отличие от ключей vastgame — keystore.py): при
# каждом выпуске macOS спросит — нажимать «Разрешить», не «Разрешать всегда». Плюс резервная копия файлом,
# зашифрованная паролем: keygen кладёт её на Рабочий стол — переложить в надёжное место (флешка, свой Диск).
# Потеряются и ключ, и копия (или пароль от неё) — установленные приложения больше не примут обновлений:
# всем ставить новую версию руками.

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import keystore
import updater

SERVICE, ACCOUNT = "vastgame-release", "ed25519"   # запись в Связке ключей
ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist.noindex"      # вывод build.sh (поиск macOS эту папку не видит)
POST_TRIES = 20                                      # × 15 с: сколько ждать, пока GitHub покажет новый выпуск
GH_USER = "WetFoxx"                                  # выпускает только этот аккаунт GitHub
DMG = "vastgame-mac.dmg"                             # образ для ручной установки: постоянное имя, ссылка на последний
WIN_SETUP = "VastGame-Setup.exe"                     # то же для Windows: установщик последней версии
LATEST = f"https://github.com/{updater.REPO}/releases/latest/download/{updater.MAC_MANIFEST}"   # всегда настоящий GitHub
LATEST_WIN = f"https://github.com/{updater.REPO}/releases/latest/download/{updater.WIN_MANIFEST}"
BACKUP = Path.home() / "Desktop" / "vastgame-release-key.pem"
PUBLIC_FILE = Path(__file__).resolve().parent / "release-public-key.txt"   # открытый ключ показывать можно
MIN_PASSPHRASE = 12
ASK_TIMEOUT = 600          # с: человеку ответить на вопрос macOS


def public_b64(key):
    """Открытый ключ (base64 32 байт) — то, что вшивается в приложение."""
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def sign_manifest(key, zip_path, version):
    """Подписанное описание архива: формат, версия, имя, размер, SHA-256 и подпись всего этого."""
    zip_path = Path(zip_path)
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 ** 2), b""):
            h.update(chunk)
    data = {"format": updater.FORMAT, "version": version, "file": zip_path.name,
            "size": zip_path.stat().st_size, "sha256": h.hexdigest()}
    data["signature"] = base64.b64encode(key.sign(updater.canonical(data))).decode()
    return data


def _exists():
    """Есть ли ключ в Связке ключей (значение не читается, macOS не спрашивает)."""
    return keystore._security("find-generic-password", "-s", SERVICE, "-a", ACCOUNT)[0] == 0


def _store(key):
    """Закрытый ключ — в Связку ключей через стандартный ввод security; -T "" — ни одной программе без вопроса."""
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    value = "ed25519:" + raw.hex()
    code, _ = keystore._security("-i", stdin=f'add-generic-password -s {SERVICE} -a {ACCOUNT} '
                                              f'-l "vastgame release signing key" -T "" -X {value.encode().hex()}\n')
    if code != 0 or not _exists():
        sys.exit(f"Не удалось сохранить ключ в Связку ключей (security, код {code}).")


def load_key():
    """Закрытый ключ из Связки ключей: macOS спросит — «Разрешить»."""
    if not _exists():
        sys.exit("Ключа подписи нет в Связке ключей: создать — keygen, вернуть из копии — restore.")
    print("macOS спросит доступ к «vastgame-release»: введи пароль от Mac и нажми «Разрешить» "
          "(не «Разрешать всегда»).")
    code, out = keystore._security("find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w", timeout=ASK_TIMEOUT)
    value = out.strip()
    if code != 0 or not re.fullmatch(r"ed25519:[0-9a-f]{64}", value):
        sys.exit(f"Ключ не прочитан (security, код {code}) — доступ не разрешён?")
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(value[8:]))


def _passphrase():
    import getpass
    for _ in range(3):
        p = getpass.getpass(f"Пароль для резервной копии ключа (не меньше {MIN_PASSPHRASE} символов, ввод скрыт): ")
        if len(p) < MIN_PASSPHRASE:
            print(f"Короче {MIN_PASSPHRASE} символов — ещё раз.")
        elif getpass.getpass("Ещё раз тот же пароль: ") != p:
            print("Не совпали — ещё раз.")
        else:
            return p
    sys.exit("Пароль так и не введён — ничего не создано.")


def keygen():
    if _exists():
        sys.exit("Ключ подписи уже есть в Связке ключей (vastgame-release). Второй не создаю: выпуски с новым "
                 "ключом установленные приложения не примут. Вернуть из копии — restore.")
    if BACKUP.exists():
        sys.exit(f"{BACKUP} уже есть — не перезаписываю. Переложи его в другое место и запусти снова.")
    key = Ed25519PrivateKey.generate()
    pw = _passphrase()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.BestAvailableEncryption(pw.encode()))
    # Сначала копия: если Связка ключей не примет ключ, он не потеряется
    fd = os.open(BACKUP, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    back = serialization.load_pem_private_key(BACKUP.read_bytes(), pw.encode())
    if public_b64(back) != public_b64(key):
        sys.exit("Резервная копия читается не так — ключ не сохранён. Сообщи об этом.")
    _store(key)
    PUBLIC_FILE.write_text(public_b64(key) + "\n")
    print(f"""
Готово.
  Закрытый ключ — в Связке ключей (vastgame-release); без твоего «Разрешить» его не прочитает никто.
  Резервная копия, зашифрованная твоим паролем: {BACKUP}
    → переложи её в надёжное место (флешка, свой Google Диск) и запомни пароль: без копии и пароля
      потерянный ключ не вернуть, и всем придётся ставить новую версию руками.
  Открытый ключ (его можно показывать, он вшивается в приложение): {public_b64(key)}
    записан в {PUBLIC_FILE.name}""")


def restore(path):
    if _exists():
        sys.exit("Ключ подписи уже есть в Связке ключей — не заменяю.")
    import getpass
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(),
                                                 getpass.getpass("Пароль резервной копии (ввод скрыт): ").encode())
    except (OSError, ValueError, TypeError):
        sys.exit("Файл не прочитан или пароль не подошёл.")
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("В файле не ключ Ed25519.")
    _store(key)
    same = public_b64(key) == updater.PUBLIC_KEY
    print(f"Ключ возвращён в Связку ключей. Открытый: {public_b64(key)}"
          + (" — совпадает с вшитым в приложение." if same else " — НЕ совпадает с вшитым в приложение!"))


def manifest(zip_path):
    """Подписать описание для архива Mac (vastgame-update.json) или установщика Windows (vastgame-update-win.json)
    — рядом с файлом."""
    zip_path = Path(zip_path)
    kinds = ((r"vastgame-(\d+\.\d+\.\d+)-mac-arm64\.zip", updater.MAC_FILE, updater.MAC_MANIFEST),
             (r"VastGame-Setup-(\d+\.\d+\.\d+)\.exe", updater.WIN_FILE, updater.WIN_MANIFEST))
    for pattern, file, name in kinds:
        m = re.fullmatch(pattern, zip_path.name)
        if m and zip_path.is_file():
            break
    else:
        sys.exit("Нужен архив vastgame-<версия>-mac-arm64.zip или установщик VastGame-Setup-<версия>.exe.")
    data = sign_manifest(load_key(), zip_path, m.group(1))
    updater.verify(data, file=file)   # вшитый в приложение открытый ключ эту подпись принимает (иначе — ошибка здесь)
    out = zip_path.with_name(name)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"Подписано: {out} (версия {data['version']}, {data['size']} байт)")


# ---------------------------------------------------------------- выпуск

def _run(cmd, **kw):
    """Запустить программу, вывод — в CompletedProcess (текстом)."""
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, **kw)


def _step(text):
    print(f"\n━━ {text}")


def _ask(question):
    return input(f"{question} [да/нет]: ").strip().lower()


def _published():
    """Номер последнего выпуска на GitHub (описание проверено вшитым ключом) или None — выпусков ещё нет."""
    try:
        return updater.fetch(LATEST)["version"]
    except updater.VastgameError as e:
        if e.key == "update.err.fetch" and e.params.get("code") == 404:
            return None
        sys.exit(f"Не удалось проверить последний выпуск на GitHub: {e}")


def _check_zip(zip_path, version):
    """Архив — как у людей: распаковать и проверить тем же кодом, что приложение перед установкой."""
    tmp = Path(tempfile.mkdtemp())
    try:
        updater.check_app(updater.unpack(zip_path, {"version": version}, tmp), {"version": version})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_setup(exe):
    """Установщик Windows для выпуска: нет — None (выпуск только для Mac); есть — программа Windows разумного
    размера, собранная после последнего коммита (не остаток прежней сборки), иначе — отказ."""
    if not exe.exists():
        return None
    with open(exe, "rb") as f:
        head = f.read(2)
    if head != b"MZ" or not 1024 ** 2 < exe.stat().st_size <= updater.MAX_SIZE:
        sys.exit(f"{exe.name} не похож на установщик Windows — пересобери (build-win.ps1).")
    committed = int(_run(["git", "log", "-1", "--format=%ct"]).stdout.strip() or 0)
    if exe.stat().st_mtime < committed:
        sys.exit(f"{exe.name} старше последнего коммита — пересобери в Windows (build-win.ps1) и положи заново.")
    return exe


def _post_check(version, zip_path, exe=None):
    """После публикации — глазами приложения: «последний выпуск» на GitHub — эта версия с верной подписью,
    архив (и установщик Windows, если выпущен) по ссылке выпуска отдаётся нужного размера. Ссылку «последний
    выпуск» GitHub несколько минут отдаёт из кэша по-разному (живой случай 2026-09-28: 30 секунд не хватило) —
    ждём до ~5 минут."""
    checks = [(LATEST, updater.MAC_FILE, zip_path)] + ([(LATEST_WIN, updater.WIN_FILE, exe)] if exe else [])
    for attempt in range(POST_TRIES):
        try:
            ok = True
            for latest, file, path in checks:
                m = updater.fetch(latest, file=file)
                url = f"https://github.com/{updater.REPO}/releases/download/vastgame-v{version}/{path.name}"
                # GET без чтения тела: подписанные ссылки GitHub на файлы выпуска не обязаны отвечать на HEAD
                with requests.get(url, stream=True, allow_redirects=True, timeout=30) as r:
                    size = int(r.headers.get("Content-Length", -1))
                ok = ok and m["version"] == version and r.status_code == 200 and size == path.stat().st_size
            if ok:
                return True
        except (updater.VastgameError, requests.RequestException, ValueError):
            pass
        time.sleep(15)
    return False


def publish(notes):
    """Выпустить версию из version.py: проверки → сборка → архив и образ → проверка архива → подпись →
    сводка и «да» → GitHub Releases → проверка глазами приложения → метка в git проекта."""
    version = re.search(r'^VERSION = "(\d+\.\d+\.\d+)"', (ROOT / "version.py").read_text(encoding="utf-8"), re.M).group(1)
    tag = f"vastgame-v{version}"
    notes = (notes or "").strip()

    _step(f"Проверяю, можно ли выпускать {version}")
    if not notes:
        sys.exit("Нужен текст «что нового»: --notes \"…\"")
    if _run(["git", "status", "--porcelain"]).stdout.strip():
        sys.exit("В git есть несохранённые изменения — выпуск должен быть ровно тем, что сохранено. Сначала коммит.")
    if _run(["gh", "api", "user", "--jq", ".login"]).stdout.strip() != GH_USER:
        sys.exit(f"gh не вошёл в аккаунт {GH_USER}: gh auth login")
    last = _published()
    if last and not updater.newer(version, last):
        sys.exit(f"В version.py {version}, а на GitHub уже {last} — номер нужен больше.")
    if _run(["gh", "release", "view", tag, "--repo", updater.REPO]).returncode == 0:
        sys.exit(f"Выпуск {tag} на GitHub уже есть — поверх не выкладываю.")
    print(f"  можно: на GitHub {last or 'выпусков ещё нет'}, выпускаю {version}")

    _step("Собираю приложение (build.sh)")
    if _run(["./build.sh"]).returncode != 0:
        sys.exit("Сборка не удалась — подробности: ./build.sh")
    zip_path = DIST / updater.MAC_FILE.format(version=version)
    zip_path.unlink(missing_ok=True)
    if _run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(DIST / "vastgame.app"),
             str(zip_path)]).returncode != 0:
        sys.exit("Не удалось упаковать архив.")
    shutil.copyfile(DIST / "vastgame.dmg", DIST / DMG)

    _step("Проверяю архив так же, как приложение перед установкой")
    _check_zip(zip_path, version)
    print("  приложение то, версия та, подпись кода цела, самопроверка прошла")

    exe = _check_setup(DIST / updater.WIN_FILE.format(version=version))

    _step("Подписываю описание" + (" для Mac и Windows" if exe else ""))
    key = load_key()
    data = sign_manifest(key, zip_path, version)
    updater.verify(data, file=updater.MAC_FILE)
    manifest_path = DIST / updater.MAC_MANIFEST
    manifest_path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    files = [zip_path, manifest_path, DIST / DMG]
    if exe:
        wdata = sign_manifest(key, exe, version)
        updater.verify(wdata, file=updater.WIN_FILE)
        (DIST / updater.WIN_MANIFEST).write_text(json.dumps(wdata, indent=1, ensure_ascii=False) + "\n")
        shutil.copyfile(exe, DIST / WIN_SETUP)
        files += [exe, DIST / updater.WIN_MANIFEST, DIST / WIN_SETUP]
        windows = (f"{exe.name}, {exe.stat().st_size / 1e6:.1f} МБ, собран "
                   f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(exe.stat().st_mtime))}; {WIN_SETUP} — для "
                   f"ручной установки; описание {updater.WIN_MANIFEST}, подписано")
    else:
        windows = f"нет — выпуск только для Mac (нет dist.noindex/{updater.WIN_FILE.format(version=version)})"
    files.append(ROOT / "wolf-setup.sh")  # setup.py ссылается на файл именно этого выпуска

    _step("Сводка")
    print(f"""  выпуск:   {tag} → github.com/{updater.REPO}/releases (станет «последним»)
  архив:    {zip_path.name}, {zip_path.stat().st_size / 1e6:.1f} МБ, SHA-256 {data['sha256'][:16]}…
  образ:    {DMG}, {(DIST / DMG).stat().st_size / 1e6:.1f} МБ — для ручной установки
  описание: {updater.MAC_MANIFEST}, подписано
  Windows:  {windows}
  что нового: {notes}""")
    if _ask("Опубликовать?") != "да":
        sys.exit("Не опубликовано. Собранное лежит в dist.noindex/.")

    _step("Публикую на GitHub")
    notes_file = DIST / "release-notes.md"
    notes_file.write_text(notes + "\n")
    r = _run(["gh", "release", "create", tag, *map(str, files),
              "--repo", updater.REPO, "--title", f"vastgame {version} beta", "--notes-file", str(notes_file), "--latest"])
    if r.returncode != 0:
        sys.exit(f"gh не опубликовал выпуск: {r.stderr.strip()[:300]}")

    _run(["git", "tag", tag])                  # выпуск уже есть — метка в git проекта в любом случае

    _step("Проверяю глазами приложения (GitHub показывает новый выпуск не сразу — до 5 минут)")
    if _post_check(version, zip_path, exe):
        print(f"  приложения увидят {version} в течение 6 часов (или при следующем открытии окна).")
    else:
        print(f"  ! GitHub пока отдаёт прежний выпуск по ссылке «последний» — приложения увидят {version} позже.\n"
              f"    Если и через час не видят — проверь страницу выпуска.")
    print(f"  Готово: https://github.com/{updater.REPO}/releases/tag/{tag}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Выпуск новой версии vastgame")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="создать ключ подписи выпусков (один раз)")
    sub.add_parser("restore", help="вернуть ключ из резервной копии").add_argument("file")
    sub.add_parser("manifest", help="подписать описание для архива Mac или установщика Windows").add_argument("zip")
    sub.add_parser("publish", help="выпустить версию из version.py").add_argument("--notes", required=True)
    a = p.parse_args(argv)
    if a.cmd == "keygen":
        keygen()
    elif a.cmd == "restore":
        restore(a.file)
    elif a.cmd == "manifest":
        manifest(a.zip)
    elif a.cmd == "publish":
        publish(a.notes)


if __name__ == "__main__":
    main()

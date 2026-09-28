# release.py — выпуск новой версии vastgame. Запускает Алексей на своём Mac; в приложение не входит.
#
#   .venv/bin/python release.py keygen            создать ключ подписи выпусков (один раз и навсегда)
#   .venv/bin/python release.py restore ФАЙЛ      вернуть ключ из резервной копии (например, на новом Mac)
#   .venv/bin/python release.py manifest ZIP      подписать файл-описание vastgame-update.json для архива
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
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import keystore
import updater

SERVICE, ACCOUNT = "vastgame-release", "ed25519"   # запись в Связке ключей
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
    zip_path = Path(zip_path)
    m = re.fullmatch(r"vastgame-(\d+\.\d+\.\d+)-mac-arm64\.zip", zip_path.name)
    if not m or not zip_path.is_file():
        sys.exit("Нужен архив vastgame-<версия>-mac-arm64.zip.")
    data = sign_manifest(load_key(), zip_path, m.group(1))
    updater.verify(data)      # вшитый в приложение открытый ключ эту подпись принимает (иначе — ошибка здесь)
    out = zip_path.with_name(updater.MANIFEST)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"Подписано: {out} (версия {data['version']}, {data['size']} байт)")


def main(argv=None):
    p = argparse.ArgumentParser(description="Выпуск новой версии vastgame")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="создать ключ подписи выпусков (один раз)")
    sub.add_parser("restore", help="вернуть ключ из резервной копии").add_argument("file")
    sub.add_parser("manifest", help="подписать описание для архива").add_argument("zip")
    a = p.parse_args(argv)
    if a.cmd == "keygen":
        keygen()
    elif a.cmd == "restore":
        restore(a.file)
    elif a.cmd == "manifest":
        manifest(a.zip)


if __name__ == "__main__":
    main()

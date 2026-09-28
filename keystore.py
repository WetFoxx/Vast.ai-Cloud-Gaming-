# keystore.py — ключи и токены vastgame: на Mac — в Связке ключей через системную программу security,
# на Windows и Linux — в хранилище системы через keyring. Значения никогда не выводятся.
#
# Почему на Mac через security (решение 2026-09-28). Связка ключей помнит, какой программе можно читать
# запись, по её подписи. Подпись без сертификата от Apple (ad-hoc или самоподписанная) для неё — отпечаток
# конкретной сборки (раздел «cdhash:…», функция partitionIdForProcess в Apple securityd), и после каждого
# обновления vastgame macOS спрашивала бы пароль Mac на каждый ключ. security для Связки ключей — всегда
# одна и та же программа, вопросов нет. Цена: любая программа под этой учётной записью Mac может прочитать
# ключи vastgame без вопроса (так же хранит свой ключ GitHub CLI).
#
# Значение уходит в security через стандартный ввод (security -i), а не в командной строке — её видят
# другие программы. В тексты ошибок не попадают ни значения, ни вывод security.
#
# Записи прежних версий (имя «vastgame», читать их разрешено только сборкам приложения) переносятся один
# раз, при первом обращении к ключам: прочитать через keyring (macOS спросит пароль — в последний раз),
# записать через security, проверить и только потом удалить старую. Когда перенесено всё, появляется
# запись-отметка DONE, и записи прежних версий больше не трогаются.

import contextlib
import subprocess
import sys
import threading

import keyring
import keyring.errors

from settings import config_dir

OLD = "vastgame"            # записи прежних версий; на Windows и Linux — единственное место
SERVICE = "vastgame-keys"   # записи на Mac: их создаёт и читает security
SECURITY = "/usr/bin/security"
MAC = sys.platform == "darwin"

# Все ключи vastgame (сброс стирает их; тест сверяет список с кодом — новый ключ не забудется)
NAMES = ("VAST_API_KEY", "RCLONE_REFRESH_TOKEN", "RCLONE_TOKEN_SCOPE", "RCLONE_REFRESH_TOKEN_OLD",
         "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TAILSCALE_AUTHKEY", "TAILSCALE_AUTHKEY_DATE",
         "SUNSHINE_PASSWORD")
# Не переносить, только стереть старую запись: полный доступ ко всему Google Диску (общий клиент rclone),
# нужен был лишь для разового переноса архивов в 2026-09-23 (решение 2026-09-28)
NOT_CARRIED = ("RCLONE_REFRESH_TOKEN_OLD",)
DONE = "_MOVED"             # запись-отметка: ключи прежних версий перенесены
LOCK = config_dir() / "keys.lock"   # замок переноса между процессами

NOT_FOUND = 44              # код выхода security «записи нет» (errSecItemNotFound = -25300, младший байт)
TIMEOUT = 120               # с: если Связка ключей заперта, macOS спросит пароль — человеку нужно время
MAX_LEN = 4000              # символов в значении (токены — сотни)

_lock = threading.Lock()
_moved = False              # отметка уже найдена этим процессом — старые записи не проверяем
_refused = None             # человек не разрешил доступ к старой записи — до перезапуска не спрашиваем снова


class KeystoreError(keyring.errors.KeyringError):
    """Связка ключей не сделала, что нужно. Подвид KeyringError — окно объясняет её как keychain.denied."""


class RealKeychainInTest(BaseException):
    """Автотест дошёл до настоящей Связки ключей. BaseException — чтобы её не проглотил ни один
    «except Exception» по дороге и тест упал громко (иначе он прочитал бы или перенёс настоящие ключи)."""


def _in_tests():
    # Приложение и терминальные команды unittest не загружают (в сборку он не входит); тесты подменяют _security
    return "unittest" in sys.modules


def get(name):
    """Значение ключа или None, если его нет."""
    _check_name(name)
    if not MAC:
        return keyring.get_password(OLD, name)
    value = _read(name)
    if value is None and not _moved:
        move_old()
        value = _read(name)
    return value


def put(name, value):
    """Сохранить ключ (заменить, если уже есть). Не сохранилось — KeystoreError."""
    _check_name(name)
    if not MAC:
        keyring.set_password(OLD, name, value)
        return
    _write(name, value)


def exists(name):
    """Есть ли ключ (или его запись прежних версий). На Mac значение не читается — macOS не спрашивает."""
    _check_name(name)
    if not MAC:
        return keyring.get_password(OLD, name) is not None
    return _found(SERVICE, name) or _found(OLD, name)


def delete(name):
    """Стереть ключ и его запись прежних версий. True — если не осталось ни той, ни другой."""
    _check_name(name)
    if not MAC:
        try:
            keyring.delete_password(OLD, name)
        except keyring.errors.PasswordDeleteError:
            pass
        return keyring.get_password(OLD, name) is None
    _security("delete-generic-password", "-s", SERVICE, "-a", name)
    _delete_old(name)
    return not exists(name)


def move_old():
    """Перенести ключи из записей прежних версий — один раз на этом Mac. macOS спросит пароль по разу на
    каждую старую запись. Отказ («Запретить») — ошибка keyring (окно объяснит: keychain.denied): всё
    разрешённое перенесено, отказанное — при следующем запуске (отметки ещё нет), а до него не спрашиваем."""
    global _moved, _refused
    with _lock:
        if _moved:
            return
        if _refused:
            raise type(_refused)(*_refused.args)
        with _between_processes():
            if _found(SERVICE, DONE):
                _moved = True
                return
            refused = None
            for name in NAMES:
                if not _found(OLD, name):
                    continue
                if name not in NOT_CARRIED and _read(name) is None:
                    try:
                        value = keyring.get_password(OLD, name)  # macOS спросит — в последний раз
                    except keyring.errors.KeyringError as e:
                        refused = refused or e
                        continue
                    if value is None:
                        continue
                    _write(name, value)                      # не сохранилось — исключение, старая запись цела
                _delete_old(name)
            if refused:
                _refused = refused
                raise refused
            _write(DONE, "1")
            _moved = True


def wipe():
    """Стереть все ключи vastgame на этом компьютере — и записи прежних версий. Возвращает
    {"deleted": стёрто ключей, "left": сколько осталось}. Не останавливается на неудаче."""
    global _moved, _refused
    _refused = None
    deleted = 0
    for name in NAMES:
        if exists(name):
            deleted += delete(name)
    if MAC:
        _security("delete-generic-password", "-s", SERVICE, "-a", DONE)
        _moved = False
        # Записи с другими именами (от прежних версий) — по одной, пока удаляются
        for service in (SERVICE, OLD):
            for _ in range(50):
                if _security("delete-generic-password", "-s", service)[0] != 0:
                    break
                deleted += 1
    left = sum(exists(n) for n in NAMES)
    if MAC and not left:
        left = int(_found(SERVICE) or _found(OLD))
    return {"deleted": deleted, "left": left}


@contextlib.contextmanager
def _between_processes():
    """Перенос — по одному процессу за раз: окно и фоновая служба (resume, раз в 5 минут) иначе спросили бы
    про одни и те же ключи дважды. Второй ждёт, пока первый закончит, и видит отметку DONE."""
    import fcntl                           # только Mac (перенос бывает только там)
    with open(LOCK, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)      # снимается при закрытии файла и при смерти процесса
        yield


def _check_name(name):
    if not (isinstance(name, str) and name.isascii() and name.replace("_", "").isalnum()):
        raise ValueError(f"недопустимое имя ключа: {name!r}")


def _security(*args, stdin=None):
    """Запустить security → (код выхода, stdout). Вывод ошибок security не показывается и не хранится."""
    if _in_tests():
        raise RealKeychainInTest(f"автотест обратился к настоящей Связке ключей: security {args[0]}")
    feed ={"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        p = subprocess.run([SECURITY, *args], capture_output=True, text=True, timeout=TIMEOUT, **feed)
    except (OSError, subprocess.SubprocessError) as e:
        raise KeystoreError(f"Связка ключей не ответила (security {args[0]}: {type(e).__name__})") from None
    return p.returncode, p.stdout


def _found(service, name=None):
    """Есть ли запись (с этим именем). Без -w: значение не читается, macOS не спрашивает."""
    return _security("find-generic-password", "-s", service, *(["-a", name] if name else []))[0] == 0


def _read(name):
    code, out = _security("find-generic-password", "-s", SERVICE, "-a", name, "-w")
    if code == NOT_FOUND:
        return None
    if code != 0:
        raise KeystoreError(f"Связка ключей не отдала {name} (security, код {code})")
    return out[:-1] if out.endswith("\n") else out


def _write(name, value):
    """Записать через стандартный ввод security -i и проверить чтением."""
    # security -w отдаёт значение как есть, только если в нём нет особых символов (иначе — в hex)
    if not (isinstance(value, str) and 0 < len(value) <= MAX_LEN and all(" " <= c <= "~" for c in value)):
        raise KeystoreError(f"{name}: такое значение не сохранить (пустое, слишком длинное или с особыми символами)")
    code, _ = _security("-i", stdin=f"add-generic-password -U -s {SERVICE} -a {name} -X {value.encode().hex()}\n")
    if code != 0 or _read(name) != value:
        raise KeystoreError(f"Не удалось сохранить {name} в Связку ключей (security, код {code})")


def _delete_old(name):
    """Стереть запись прежних версий: сначала через keyring (доступ к ней есть только у сборок приложения —
    живой случай 2026-09-25: security её стереть не смогла), потом через security. Не вышло — не страшно."""
    if not _found(OLD, name):
        return
    try:
        keyring.delete_password(OLD, name)
    except Exception:                      # noqa: BLE001 — нет доступа: пробуем вторым способом
        pass
    if _found(OLD, name):
        _security("delete-generic-password", "-s", OLD, "-a", name)

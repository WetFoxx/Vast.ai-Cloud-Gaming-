"""Замок на файл между процессами — один вызов на Mac/Linux и Windows.

Mac/Linux — fcntl.flock, как было всегда (тесты изображают «другой процесс» тем же flock). Windows — msvcrt.locking на
первый байт файла (можно и за концом пустого файла). Замок снимается сам при закрытии файла и при смерти процесса.
Файлы замков только для замка: на Windows замок обязательный — запертый байт не прочитать и не записать другим."""
import errno
import sys
import time

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_BUSY = (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", 36), getattr(errno, "EDEADLOCK", 36))


def try_lock(f):
    """Взять замок, не дожидаясь. True — взят, False — занят другим. Прочие ошибки — OSError, как раньше."""
    if sys.platform == "win32":
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if e.errno in _BUSY:
                return False
            raise
        return True
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def lock(f):
    """Взять замок, дождавшись, пока освободится."""
    if sys.platform == "win32":
        while not try_lock(f):           # LK_LOCK сдаётся через 10 с — ждём сами
            time.sleep(0.2)
        return
    fcntl.flock(f, fcntl.LOCK_EX)


def unlock(f):
    if sys.platform == "win32":
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(f, fcntl.LOCK_UN)

# sshkey.py — SSH-ключ в аккаунте Vast.
#
# Без хотя бы одного SSH-ключа в аккаунте Vast не запускает VM (у друга Алексея первая аренда
# 2026-09-25 не запустилась, пока он не добавил ключ на сайте руками). Сами мы этим ключом не
# пользуемся — машиной управляем через Tailscale SSH, — он нужен только Vast.
#
# Поэтому: есть в аккаунте ключ — ничего не делаем. Нет — создаём свою пару в папке настроек
# vastgame (~/.ssh человека не трогаем: там могут быть его ключи для других дел) и отправляем в
# Vast только открытую часть, как официальный клиент (vast-cli: POST ssh/ {"ssh_key": ...}).

import subprocess

import paths
import settings
from vastapi import VastgameError, call

KEY = settings.config_dir() / "vast_ed25519"   # секретная часть — остаётся на этом компьютере
PUB = KEY.parent / (KEY.name + ".pub")


def account_keys():
    """Сколько действующих SSH-ключей в аккаунте Vast (удалённые Vast отдаёт с deleted_at)."""
    r = call("GET", "ssh/")
    keys = r if isinstance(r, list) else (r.get("keys") or [])
    return sum(1 for k in keys if isinstance(k, dict) and not k.get("deleted_at"))


def _local_public():
    """Открытая часть своего ключа; нет пары — создать (ssh-keygen есть на Mac и в Windows 10+)."""
    if not PUB.exists():
        exe = paths.find("ssh-keygen")
        if not exe:
            raise VastgameError("Не нашёл программу ssh-keygen.", key="sshkey.no_keygen")
        KEY.parent.mkdir(parents=True, exist_ok=True)
        for p in (KEY, PUB):          # половинка от прерванной попытки — ssh-keygen спросил бы «перезаписать?»
            p.unlink(missing_ok=True)
        try:
            r = paths.run([exe, "-q", "-t", "ed25519", "-N", "", "-C", "vastgame", "-f", str(KEY)], 30)
        except (subprocess.TimeoutExpired, OSError):
            r = None
        if r is None or r.returncode != 0 or not PUB.exists():
            raise VastgameError("ssh-keygen не создал ключ.", key="sshkey.keygen_failed")
    return PUB.read_text().strip()


def ensure():
    """Позаботиться, чтобы в аккаунте Vast был SSH-ключ. True — добавили сейчас, False — уже был."""
    if account_keys():
        return False
    r = call("POST", "ssh/", {"ssh_key": _local_public()})
    if isinstance(r, dict) and r.get("success") is False:
        raise VastgameError(f"Vast не принял SSH-ключ: {r.get('msg') or ''}", key="sshkey.rejected",
                            msg=r.get("msg") or "")
    return True

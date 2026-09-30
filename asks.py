# asks.py — вопросы машины человеку (wolf-setup.sh v4.6): «появилась игра — синхронизировать её?» и «игру удалили
# с машины — удалить и из облака?». Машина пишет их на свою страницу статуса (записи ask--new--АРХИВ и
# ask--del--АРХИВ, текст — название игры), приложение сообщает о них заметно — уведомлением со звуком и карточкой
# поверх всех окон, в том числе поверх Moonlight, — и отвечает машине командой answer.
# Без ответа ничего не теряется: новая игра выгрузится при «Закончить», удалённая останется в облаке.

import fcntl

import cloudgames
import core
import notify
import settings
import wolfnode
from vastapi import VastgameError

SEEN_MAX = 100                                    # сколько последних вопросов помнить «уже сообщали»
WINDOW_LOCK = settings.config_dir() / "window.lock"   # держит открытое окно: служба знает, что карточку покажет оно
CARD_LOCK = settings.config_dir() / "card.lock"       # держит процесс одной карточки (окна нет) — второй не нужен


def pending():
    """(IP машины, [вопросы]) — вопросы по порядку появления; машины нет или она молчит — (None, [])."""
    node = wolfnode.ts_node()
    if not node:
        return None, []
    return node[1], core.asks_of(wolfnode.wolf_status(node[1]))


def _key(a):
    return f"{a['kind']}:{a['archive']}:{a['ts']}"


def fresh(asks):
    """Вопросы, о которых ещё не сообщали: уведомление и карточка — один раз на вопрос (окно и служба — вместе)."""
    seen = set(settings.get("asks_seen") or [])
    return [a for a in asks if _key(a) not in seen]


def mark_seen(asks):
    seen = [k for k in settings.get("asks_seen") or [] if isinstance(k, str)]
    seen += [_key(a) for a in asks if _key(a) not in seen]
    settings.set("asks_seen", seen[-SEEN_MAX:])


def announce(asks, lang=None):
    """Уведомление со звуком: во время игры человек смотрит в Moonlight, тихое он пропустит."""
    if not asks:
        return
    tr = notify.texts(lang or notify.language())
    if len(asks) == 1:
        a = asks[0]
        notify.send(notify.t(tr, f"ask.{a['kind']}.title"), notify.t(tr, f"ask.{a['kind']}.notify", name=a["name"]),
                    sound=True)
    else:
        notify.send(notify.t(tr, "ask.many.title", n=len(asks)), ", ".join(a["name"] for a in asks), sound=True)


def answer(ip, kind, archive, yes, on_notice=None):
    """Ответ машине и запомнить выбор для следующих запусков. «Да» на «удалили» — машина убирает из облака и игру,
    и её сохранения (в корзину Google Drive, 30 дней)."""
    if kind not in ("new", "del") or not isinstance(archive, str) or not core.ASK.match(f"ask--{kind}--{archive}"):
        raise VastgameError("Непонятный вопрос", key="ask.bad")
    if not ip:
        raise VastgameError("Машина не отвечает", key="ask.no_machine")
    code, out = wolfnode.run(ip, "answer", [kind, archive, "yes" if yes else "no"], timeout=300,
                             on_notice=on_notice, ssh=wolfnode.ssh_run)
    if code != 0:
        raise VastgameError(f"Машина не приняла ответ (код {code}): {out.strip()[-200:]}", key="ask.failed",
                            code=code, out=out.strip()[-200:])
    cloudgames.remember_answer(kind, archive, bool(yes))


def window_open():
    """Открыто ли окно приложения (оно держит WINDOW_LOCK всё время, пока работает)."""
    try:
        with open(WINDOW_LOCK, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)      # взяли — значит, окна нет (замок снимется при закрытии)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False

# i18n.py — переводы сообщений из Python, которые показывает окно приложения (ru, en, es).
# Тексты самого окна — в ui/i18n.js. Команды в терминале остаются на русском.
#
# Ошибка с ключом: raise VastgameError("русский текст", key="vast.no_key", name=...) —
# терминал печатает русский текст, окно берёт перевод по ключу: t(lang, key, **params).

TEXTS = {
    "ru": {
        "vast.no_key": "Нет ключа Vast — пройди первую настройку.",
        "vast.network": "Не удалось связаться с Vast ({error}). Проверь интернет.",
        "vast.bad_key": "Vast не принял ключ — замени его в первой настройке.",
        "vast.http": "Vast ответил ошибкой: HTTP {code} {msg}",
        "vast.templates_many": "Шаблонов «{name}» у тебя несколько ({count}) — удали лишние на сайте Vast.",
    },
    "en": {
        "vast.no_key": "No Vast key — run the first-time setup.",
        "vast.network": "Couldn't reach Vast ({error}). Check your internet connection.",
        "vast.bad_key": "Vast rejected the key — replace it in the first-time setup.",
        "vast.http": "Vast returned an error: HTTP {code} {msg}",
        "vast.templates_many": "You have several “{name}” templates ({count}) — delete the extra ones on the Vast website.",
    },
    "es": {
        "vast.no_key": "No hay clave de Vast: completa la configuración inicial.",
        "vast.network": "No se pudo conectar con Vast ({error}). Revisa tu conexión a internet.",
        "vast.bad_key": "Vast rechazó la clave: cámbiala en la configuración inicial.",
        "vast.http": "Vast devolvió un error: HTTP {code} {msg}",
        "vast.templates_many": "Tienes varias plantillas «{name}» ({count}): borra las sobrantes en la web de Vast.",
    },
}


def t(lang, key, **params):
    """Перевод по ключу; нет языка — английский; нет ключа — сам ключ (чтобы пропажа была заметна)."""
    text = TEXTS.get(lang, TEXTS["en"]).get(key) or TEXTS["en"].get(key) or key
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text


def error_text(lang, e):
    """Текст ошибки VastgameError на языке окна: по ключу, если он есть, иначе как есть."""
    key = getattr(e, "key", None)
    return t(lang, key, **getattr(e, "params", {})) if key else str(e)

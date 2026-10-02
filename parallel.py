"""Независимые запросы — одновременно: экран ждёт самый долгий из них, а не их сумму (замер 2026-10-02: «Настройка»
10 с, список машин 12–23 с — запросы к Vast, Drive и Steam шли по очереди).

run(f1, f2, ...) → [f1(), f2(), ...]. Ошибка — как при обычном вызове по порядку: первая по порядку аргументов
(VastgameError — подвид SystemExit — тоже); остальные вызовы при этом всё равно доходят до конца."""
from concurrent.futures import ThreadPoolExecutor


def run(*fns):
    if len(fns) <= 1:
        return [f() for f in fns]
    with ThreadPoolExecutor(len(fns)) as ex:
        futs = [ex.submit(f) for f in fns]
        return [f.result() for f in futs]
